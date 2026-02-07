import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import json
import time
import altair as alt
import streamlit.components.v1 as components
from datetime import datetime
import zipfile
import io

# --- PDF 報表相關套件 ---
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak, HRFlowable
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import cm

# 設定頁面
st.set_page_config(page_title="勁翔營造 工地計帳系統", layout="wide", page_icon="🏗️")

# --- 設定檔與字型 ---
# 注意：雲端版需要將 finance_settings.json 和字型檔一同上傳到 GitHub
SETTINGS_FILE = 'finance_settings.json'
FONT_FILE = 'msjh.ttc' # 請確保此檔案存在於 GitHub 儲存庫根目錄

# --- Google Sheets 連線設定 ---
# 使用 Streamlit Secrets 管理敏感資訊，避免將金鑰直接寫在程式碼中
def get_google_sheet_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    # 從 st.secrets 讀取憑證資訊
    creds_dict = dict(st.session_state.get('gcp_service_account', st.secrets["gcp_service_account"]))
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

# --- 台灣例假日設定 ---
HOLIDAYS = {
    "2025-01-01": "元旦", "2025-01-27": "小年夜", "2025-01-28": "除夕", "2025-01-29": "春節", "2025-01-30": "初二", "2025-01-31": "初三",
    "2025-02-28": "和平紀念日", "2025-04-04": "兒童節/清明節", "2025-05-01": "勞動節", "2025-05-31": "端午節",
    "2025-10-06": "中秋節", "2025-10-10": "國慶日",
    "2026-01-01": "元旦", "2026-02-16": "小年夜", "2026-02-17": "除夕", "2026-02-18": "春節",
    "2026-02-28": "和平紀念日", "2026-04-04": "兒童節", "2026-04-05": "清明節", "2026-05-01": "勞動節",
    "2026-06-19": "端午節", "2026-09-25": "中秋節", "2026-10-10": "國慶日"
}

# --- 預設類別設定 ---
DEFAULT_CAT_CONFIG = [
    {"key": "入帳金額", "display": "01. 入帳金額 (零用金)", "type": "income"},
    {"key": "施工耗材", "display": "02. 施工耗材", "type": "expense"},
    {"key": "工具設備", "display": "03. 施工工具及設備", "type": "expense"},
    {"key": "雜貨類", "display": "04. 雜貨類", "type": "expense"},
    {"key": "交通費", "display": "05. 交通費 (含油資)", "type": "expense"},
    {"key": "維修費", "display": "06. 工具設備維修費", "type": "expense"},
    {"key": "五金雜貨", "display": "07. 五金雜貨", "type": "expense"}
]

# ==========================================
# 1. 核心邏輯
# ==========================================

def get_date_info(date_obj):
    if isinstance(date_obj, str):
        try:
            date_obj = datetime.strptime(date_obj, "%Y-%m-%d").date()
        except:
            return "", False
            
    weekdays = ["(週一)", "(週二)", "(週三)", "(週四)", "(週五)", "(週六)", "(週日)"]
    date_str = date_obj.strftime("%Y-%m-%d")
    w_str = weekdays[date_obj.weekday()]
    is_weekend = date_obj.weekday() >= 5
    
    if date_str in HOLIDAYS: 
        return f"🔴 {w_str} ★{HOLIDAYS[date_str]}", True 
    if is_weekend: 
        return f"🔴 {w_str}", True 
    return f"{w_str}", False

def load_json(filepath, default_data):
    # 設定檔仍維持本地 JSON (因為通常變動不大，若需多人同步設定，建議也改用 Sheet)
    if not os.path.exists(filepath):
        return default_data
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(filepath, data):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_settings():
    default = {
        "projects": ["預設專案"],
        "items": {"預設專案": {c["key"]: [] for c in DEFAULT_CAT_CONFIG}},
        "locations": {"預設專案": {c["key"]: [] for c in DEFAULT_CAT_CONFIG}},
        "cat_config": DEFAULT_CAT_CONFIG
    }
    data = load_json(SETTINGS_FILE, default)
    
    if "cat_config" not in data: data["cat_config"] = DEFAULT_CAT_CONFIG
    if "locations" not in data: data["locations"] = {}
    
    for p in data["projects"]:
        if p not in data["items"]: data["items"][p] = {c["key"]: [] for c in data["cat_config"]}
        if p not in data["locations"]: data["locations"][p] = {c["key"]: [] for c in data["cat_config"]}
        for c in data["cat_config"]:
            if c["key"] not in data["items"][p]: data["items"][p][c["key"]] = []
            if c["key"] not in data["locations"][p]: data["locations"][p][c["key"]] = []
    return data

# --- 改寫：從 Google Sheets 讀取資料 ---
@st.cache_data(ttl=60) # 設定快取 60 秒，避免頻繁呼叫 API
def load_data():
    try:
        client = get_google_sheet_client()
        # 這裡假設您的 Google Sheet 名稱為 "FinanceData"，請確保名稱一致
        sheet = client.open("FinanceData").sheet1 
        data = sheet.get_all_records()
        
        if not data:
            # 如果是空的，回傳空 DataFrame (需有欄位)
            cols = ['日期', '專案', '類別', '項目內容', '單位', '數量', '單價', '總價',
                    '購買地點', '經手人', '憑證類型', '發票號碼', '備註']
            return pd.DataFrame(columns=cols)

        df = pd.DataFrame(data)
        
        # 確保欄位格式正確
        text_cols = ['發票號碼', '備註', '購買地點', '經手人', '項目內容', '專案', '類別', '單位', '憑證類型']
        for col in text_cols:
            if col in df.columns:
                df[col] = df[col].fillna("").astype(str)
        
        if '日期' in df.columns:
            df['日期'] = pd.to_datetime(df['日期']).dt.date
            df['月份'] = pd.to_datetime(df['日期']).dt.strftime("%Y-%m")
            df['Year'] = pd.to_datetime(df['日期']).dt.year
            
        return df
    except Exception as e:
        st.error(f"讀取 Google Sheets 失敗: {e}")
        # 回傳空 DataFrame 避免程式崩潰
        cols = ['日期', '專案', '類別', '項目內容', '單位', '數量', '單價', '總價',
                '購買地點', '經手人', '憑證類型', '發票號碼', '備註']
        return pd.DataFrame(columns=cols)

# --- 改寫：寫入資料到 Google Sheets ---
def append_finance_record(date, project, category, item, unit, qty, price, location, handler, r_type, inv_no, note):
    total = qty * price
    inv_no_str = str(inv_no) if inv_no else ""
    
    # 準備要寫入的一列資料
    row_data = [
        str(date), project, category, item,
        unit, qty, price, total,
        location, handler, r_type, inv_no_str, note
    ]
    
    try:
        client = get_google_sheet_client()
        sheet = client.open("FinanceData").sheet1
        sheet.append_row(row_data)
        # 清除快取，讓介面重新讀取最新資料
        load_data.clear()
        
    except Exception as e:
        st.error(f"寫入 Google Sheets 失敗: {e}")

# (注意：雲端版暫時移除「整批更新 dataframe」的功能，因為 GSheet API 更新整張表較複雜且風險高
# 這裡僅保留「新增」功能。若需修改/刪除，建議直接去 Google Sheets 操作，或需開發更進階的邏輯)

# --- PDF 生成功能 ---
def generate_pdf_report(df, project_name, year, month):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
    
    # 讀取上傳到 GitHub 的字型檔
    font_path = FONT_FILE 
    if not os.path.exists(font_path):
        # 如果找不到，嘗試用系統預設 (雖然中文會亂碼，但至少不報錯)
        font_name = 'Helvetica'
        font_bold_name = 'Helvetica-Bold'
        st.warning("⚠️ 找不到中文字型檔，報表中文可能會顯示異常。請確認已上傳 msjh.ttc 到 GitHub。")
    else:
        try:
            pdfmetrics.registerFont(TTFont('Msjh', font_path))
            # 如果沒有粗體檔，就用同一個字型代替
            pdfmetrics.registerFont(TTFont('MsjhBd', font_path)) 
            font_name = 'Msjh'
            font_bold_name = 'MsjhBd'
        except:
            font_name = 'Helvetica'
            font_bold_name = 'Helvetica-Bold'
    
    # --- 定義樣式 (維持不變) ---
    accent_color = colors.HexColor('#003366')
    header_bg_color = colors.HexColor('#003366')
    header_text_color = colors.white
    zebra_bg_odd = colors.HexColor('#F9F9F9')
    zebra_bg_even = colors.white
    summary_bg = colors.HexColor('#F0F4F8')

    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(name='Title_TC', parent=styles['Heading1'], fontName=font_bold_name, fontSize=28, leading=36, alignment=1, textColor=accent_color, spaceAfter=6)
    style_subtitle = ParagraphStyle(name='SubTitle_TC', parent=styles['Heading2'], fontName=font_name, fontSize=16, leading=20, alignment=1, textColor=colors.black, spaceAfter=20)
    style_h2 = ParagraphStyle(name='H2_TC', parent=styles['Heading2'], fontName=font_bold_name, fontSize=18, leading=22, spaceBefore=15, spaceAfter=10, textColor=accent_color, keepWithNext=True)
    style_h3 = ParagraphStyle(name='H3_TC', parent=styles['Heading3'], fontName=font_bold_name, fontSize=14, leading=18, spaceBefore=12, spaceAfter=6, textColor=colors.black, keepWithNext=True)
    style_table_cell = ParagraphStyle(name='TableCell_TC', parent=styles['Normal'], fontName=font_name, fontSize=11, leading=13)

    elements = []
    
    if month == "整年度":
        time_display = f"{year}年年報"
    else:
        m_only = month.split('-')[1]
        time_display = f"{year}年{m_only}月份"

    elements.append(Paragraph("勁翔營造工地支出報表", style_title))
    elements.append(Paragraph(time_display, style_subtitle))
    
    info_data = [[f"專案名稱：{project_name}", f"列印時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}"]]
    t_info = Table(info_data, colWidths=[400, 300])
    t_info.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_name), ('FONTSIZE', (0,0), (-1,-1), 11), ('ALIGN', (0,0), (0,0), 'LEFT'), ('ALIGN', (1,0), (1,0), 'RIGHT')]))
    elements.append(t_info)
    elements.append(HRFlowable(width="100%", thickness=2, color=accent_color, spaceBefore=5, spaceAfter=15))

    elements.append(Paragraph("一、財務總覽", style_h2))
    
    rpt_inc = df[df['類別'] == '入帳金額']['總價'].sum()
    rpt_exp = df[df['類別'] != '入帳金額']['總價'].sum()
    rpt_bal = rpt_inc - rpt_exp
    
    data_summary = [['項目', '總入帳', '總支出', '目前結餘'], ['金額', f"${rpt_inc:,.0f}", f"${rpt_exp:,.0f}", f"${rpt_bal:,.0f}"]]
    t_sum = Table(data_summary, colWidths=[120, 180, 180, 180], hAlign='LEFT')
    t_sum.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_name), ('FONTSIZE', (0,0), (-1,-1), 12), ('LEADING', (0,0), (-1,-1), 18), ('BACKGROUND', (0,0), (-1,0), accent_color), ('TEXTCOLOR', (0,0), (-1,0), header_text_color), ('FONTNAME', (0,0), (-1,0), font_bold_name), ('ALIGN', (0,0), (-1,0), 'CENTER'), ('ALIGN', (1,1), (-1,1), 'RIGHT'), ('BACKGROUND', (0,1), (-1,1), summary_bg), ('GRID', (0,0), (-1,-1), 1, colors.grey), ('TEXTCOLOR', (3,1), (3,1), colors.red if rpt_bal < 0 else accent_color), ('FONTNAME', (3,1), (3,1), font_bold_name)]))
    elements.append(t_sum)
    elements.append(Spacer(1, 25))
    
    elements.append(Paragraph("二、支出結構分析", style_h2))
    exp_summary = df[df['類別'] != '入帳金額'].groupby('類別')['總價'].sum().reset_index().sort_values('總價', ascending=False)
    if not exp_summary.empty:
        exp_data = [['支出大項', '金額', '佔比']]
        for i, row in exp_summary.iterrows():
            pct = (row['總價'] / rpt_exp * 100) if rpt_exp > 0 else 0
            exp_data.append([row['類別'], f"${row['總價']:,.0f}", f"{pct:.1f}%"])
        t_exp = Table(exp_data, colWidths=[250, 150, 100], hAlign='LEFT')
        zebra_styles = [] 
        for i in range(1, len(exp_data)):
            bg_c = zebra_bg_odd if i % 2 != 0 else zebra_bg_even
            zebra_styles.append(('BACKGROUND', (0, i), (-1, i), bg_c))
        t_exp.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_name), ('FONTSIZE', (0,0), (-1,-1), 11), ('LEADING', (0,0), (-1,-1), 14), ('BACKGROUND', (0,0), (-1,0), header_bg_color), ('TEXTCOLOR', (0,0), (-1,0), header_text_color), ('FONTNAME', (0,0), (-1,0), font_bold_name), ('ALIGN', (1,0), (-1,-1), 'RIGHT'), ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey), ('BOX', (0,0), (-1,-1), 1, accent_color)] + zebra_styles))
        elements.append(t_exp)
    elements.append(Spacer(1, 20))
    
    elements.append(Paragraph("三、各分類詳細支出表", style_h2))
    cat_order = ['入帳金額'] + exp_summary['類別'].tolist()
    col_widths = [70, 40, 140, 35, 40, 60, 70, 80, 50, 40, 65, 90]
    headers = ['日期', '星期', '項目內容', '單位', '數量', '單價', '總價', '地點', '經手', '憑證', '發票', '備註']
    weekdays_list = ["(週一)", "(週二)", "(週三)", "(週四)", "(週五)", "(週六)", "(週日)"]

    for cat in cat_order:
        cat_df = df[df['類別'] == cat].sort_values('日期', ascending=False).copy()
        if cat_df.empty: continue
        subtotal = cat_df['總價'].sum()
        elements.append(Paragraph(f"{cat} (小計: ${subtotal:,.0f})", style_h3))
        table_data = [headers]
        for _, row in cat_df.iterrows():
            try: dt_obj = pd.to_datetime(row['日期']); w_simple = weekdays_list[dt_obj.weekday()]
            except: w_simple = ""
            table_data.append([str(row['日期']), w_simple, Paragraph(str(row['項目內容']), style_table_cell), str(row['單位']), f"{row['數量']}", f"{row['單價']:,.0f}", f"{row['總價']:,.0f}", Paragraph(str(row['購買地點']), style_table_cell), str(row['經手人'])[:6], str(row['憑證類型']), str(row['發票號碼']), Paragraph(str(row['備註']), style_table_cell)])
        t_detail = Table(table_data, colWidths=col_widths, repeatRows=1)
        detail_zebra = []
        for i in range(1, len(table_data)):
             bg_c = zebra_bg_odd if i % 2 != 0 else zebra_bg_even
             detail_zebra.append(('BACKGROUND', (0, i), (-1, i), bg_c))
        t_detail.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_name), ('FONTSIZE', (0,0), (-1,-1), 11), ('LEADING', (0,0), (-1,-1), 14), ('BACKGROUND', (0,0), (-1,0), header_bg_color), ('TEXTCOLOR', (0,0), (-1,0), header_text_color), ('FONTNAME', (0,0), (-1,0), font_bold_name), ('ALIGN', (0,0), (-1,-1), 'LEFT'), ('ALIGN', (4,0), (6,-1), 'RIGHT'), ('VALIGN', (0,0), (-1,-1), 'TOP'), ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey), ('BOX', (0,0), (-1,-1), 1, accent_color)] + detail_zebra))
        elements.append(t_detail)
        elements.append(Spacer(1, 25))
    doc.build(elements)
    buffer.seek(0)
    return buffer

def handle_save(conf_key, conf_type, date, project, conf_display):
    k_sel = f"sel_{conf_key}"; k_man = f"man_{conf_key}"; k_item = f"item_{conf_key}" 
    k_sel_loc = f"sel_loc_{conf_key}"; k_man_loc = f"man_loc_{conf_key}"; k_loc = f"loc_{conf_key}"
    k_buyer = f"buyer_{conf_key}"; k_type = f"type_{conf_key}"; k_inv = f"inv_{conf_key}"
    k_qty = f"qty_{conf_key}"; k_unit = f"unit_{conf_key}"; k_price = f"price_{conf_key}"; k_note = f"note_{conf_key}"

    if conf_type == "income":
        sel_val = st.session_state.get(k_sel, ""); man_val = st.session_state.get(k_man, "")
        final_item = man_val if sel_val == "✏️ 手動輸入..." else sel_val
        location, r_type, inv_no, qty, unit = "", "無", "", 1, "次"
        price = st.session_state.get(k_price, 0); handler = st.session_state.get(k_buyer, "")
    else:
        sel_val = st.session_state.get(k_sel, ""); man_val = st.session_state.get(k_man, "")
        final_item = man_val if sel_val == "✏️ 手動輸入..." else sel_val
        sel_loc_val = st.session_state.get(k_sel_loc, ""); man_loc_val = st.session_state.get(k_man_loc, "")
        location = man_loc_val if sel_loc_val == "✏️ 手動輸入..." else sel_loc_val
        if not location: location = st.session_state.get(k_loc, "")
        handler = st.session_state.get(k_buyer, ""); r_type = st.session_state.get(k_type, "收據")
        inv_no = st.session_state.get(k_inv, "") if r_type == "發票" else ""
        qty = st.session_state.get(k_qty, 1.0); unit = st.session_state.get(k_unit, "式")
        price = st.session_state.get(k_price, 0)

    note = st.session_state.get(k_note, "")
    if not final_item:
        st.toast(f"❌ 請輸入 {conf_display} 的項目/來源！", icon="⚠️"); return

    append_finance_record(date, project, conf_key, final_item, unit, qty, price, location, handler, r_type, inv_no, note)
    st.toast(f"✅ {conf_display} 儲存成功！")
    
    st.session_state[k_man] = ""; st.session_state[k_price] = 0; st.session_state[k_note] = ""; st.session_state[k_buyer] = ""
    if conf_type != "income": st.session_state[k_man_loc] = ""; st.session_state[k_inv] = ""; st.session_state[k_qty] = 1.0

# ==========================================
# 2. UI 介面 (主程式)
# ==========================================

settings = load_settings()
df = load_data() # 現在會從 Google Sheets 載入

st.title("🏗️ 勁翔營造 工地計帳系統")

if 'last_check_date' not in st.session_state:
    st.session_state.last_check_date = datetime.now().date()

with st.sidebar:
    st.header("📅 專案選擇")
    if not settings["projects"]: settings["projects"] = ["預設專案"]
    global_project = st.selectbox("目前專案", settings["projects"])
    global_date = st.date_input("記帳日期", st.session_state.last_check_date)
    if global_date != st.session_state.last_check_date:
        st.session_state.last_check_date = global_date
        components.html("""<script>var tabs=window.parent.document.querySelectorAll('[data-testid="stTab"]');if(tabs.length>0){tabs[0].click();}</script>""", height=0, width=0)
    day_str, is_red = get_date_info(global_date)
    if is_red: st.markdown(f"<h3 style='color: #FF4B4B;'>{global_date} {day_str}</h3>", unsafe_allow_html=True)
    else: st.markdown(f"### {global_date} {day_str}")

tab_entry, tab_data, tab_dash, tab_settings = st.tabs(["📝 支出填寫", "📋 明細管理", "📊 收支儀表板", "⚙️ 設定與管理"])

# --- Tab 1: 支出填寫 (維持原樣) ---
with tab_entry:
    st.info(f"📍 當前專案：{global_project} | 日期：{global_date} {day_str}")
    st.caption("👇 點擊下方類別展開填寫")
    for conf in settings["cat_config"]:
        icon = "💰" if conf["type"] == "income" else "💸"
        k_sel = f"sel_{conf['key']}"; k_man = f"man_{conf['key']}"; k_price = f"price_{conf['key']}"
        k_buyer = f"buyer_{conf['key']}"; k_note = f"note_{conf['key']}"; k_sel_loc = f"sel_loc_{conf['key']}"
        k_man_loc = f"man_loc_{conf['key']}"; k_type = f"type_{conf['key']}"; k_inv = f"inv_{conf['key']}"
        k_qty = f"qty_{conf['key']}"; k_unit = f"unit_{conf['key']}"

        # 初始化 session state
        if k_man not in st.session_state: st.session_state[k_man] = ""
        if k_price not in st.session_state: st.session_state[k_price] = 0
        if k_qty not in st.session_state: st.session_state[k_qty] = 1.0
        
        with st.expander(f"{icon} {conf['display']}", expanded=False):
            col1, col2 = st.columns(2)
            items = settings["items"].get(global_project, {}).get(conf["key"], [])
            items_with_manual = items + ["✏️ 手動輸入..."]
            
            if conf["type"] == "income":
                with col1:
                    sel = st.selectbox("入帳來源", items_with_manual, key=k_sel)
                    if sel == "✏️ 手動輸入...": st.text_input("請輸入入帳來源", key=k_man) 
                    st.number_input("入帳金額", min_value=0, step=100, key=k_price)
                with col2: st.text_input("收帳人 (經手人)", key=k_buyer); st.text_area("備註", key=k_note)
            else:
                with col1:
                    sel = st.selectbox("項目內容", items_with_manual, key=k_sel)
                    if sel == "✏️ 手動輸入...": st.text_input("請輸入項目名稱", key=k_man)
                    locs = settings["locations"].get(global_project, {}).get(conf["key"], [])
                    locs_with_manual = locs + ["✏️ 手動輸入..."]
                    sel_loc = st.selectbox("購買地點", locs_with_manual, key=k_sel_loc)
                    if sel_loc == "✏️ 手動輸入...": st.text_input("請輸入購買地點", key=k_man_loc)
                    st.text_input("購買人 (經手人)", key=k_buyer)
                with col2:
                    st.radio("憑證類型", ["收據", "發票"], horizontal=True, key=k_type)
                    st.text_input("發票號碼", key=k_inv)
                    c_q, c_u = st.columns(2)
                    with c_q: st.number_input("數量", min_value=0.0, step=0.5, key=k_qty)
                    with c_u: st.text_input("單位", key=k_unit)
                    st.number_input("單價/金額", min_value=0, step=1, key=k_price)
                st.text_input("備註", key=k_note)
            st.button("💾 儲存紀錄", key=f"btn_save_{conf['key']}", on_click=handle_save, args=(conf['key'], conf['type'], global_date, global_project, conf['display']))

# --- Tab 2: 明細管理 (僅顯示，移除編輯功能，因為 GSheet API 編輯較複雜) ---
with tab_data:
    st.info("⚠️ 雲端版目前僅支援「檢視」明細。如需修改或刪除資料，請直接前往 Google 試算表操作，完成後重新整理此頁面。")
    # (這裡放一個連結按鈕到 Google Sheet 會很方便，但需要連結)
    # st.link_button("前往 Google 試算表", "YOUR_SHEET_URL") 
    
    proj_df = df[df['專案'] == global_project].copy()
    if proj_df.empty: st.info("⚠️ 本專案尚無任何資料")
    else:
        c_filter1, c_filter2, c_filter3 = st.columns([1, 1, 2])
        proj_df['Year'] = pd.to_datetime(proj_df['日期']).dt.year
        all_years = sorted(proj_df['Year'].unique().tolist(), reverse=True)
        with c_filter1: sel_year = st.selectbox("📅 統計年份", all_years, key="hist_year")
        year_df = proj_df[proj_df['Year'] == sel_year].copy()
        all_months = sorted(year_df['月份'].unique().tolist(), reverse=True)
        with c_filter2: sel_month = st.selectbox("編輯月份", ["整年"] + all_months, key="hist_month")
        with c_filter3: search_kw = st.text_input("🔍 搜尋關鍵字", placeholder="輸入項目、備註或發票號碼...")
        
        view_df = year_df.copy()
        if sel_month != "整年": view_df = view_df[view_df['月份'] == sel_month]
        if search_kw:
            view_df = view_df[view_df['項目內容'].str.contains(search_kw, case=False) | view_df['備註'].str.contains(search_kw, case=False)]
            
        st.dataframe(view_df, use_container_width=True, hide_index=True)

# --- Tab 3: 收支儀表板 (含 PDF 下載) ---
with tab_dash:
    dash_df = df[df['專案'] == global_project].copy()
    if not dash_df.empty:
        # ... (計算邏輯保持不變) ...
        dash_df['總價'] = pd.to_numeric(dash_df['總價'], errors='coerce').fillna(0)
        today_str = datetime.now().date(); cur_year = today_str.year
        income_df = dash_df[dash_df['類別'] == '入帳金額']; expense_df = dash_df[dash_df['類別'] != '入帳金額']
        in_total = income_df['總價'].sum(); out_total = expense_df['總價'].sum()
        
        st.markdown(f"### 📊 {cur_year}年 財務概況")
        i1, i2 = st.columns(2)
        i1.metric("專案總入帳", f"${in_total:,.0f}")
        i2.metric("專案總支出", f"${out_total:,.0f}")
        st.divider()
        st.metric("💰 專案目前結餘", f"${in_total - out_total:,.0f}")
        st.divider()
        
        # ... (圖表部分保持不變) ...
        
    # --- PDF 報表 ---
    st.divider()
    st.subheader("📄 產出財務報表 (預覽與列印)")
    if not dash_df.empty:
        c_rpt_y, c_rpt_m = st.columns(2)
        rpt_years = sorted(dash_df['Year'].unique().tolist(), reverse=True)
        with c_rpt_y: rpt_sel_year = st.selectbox("報表年份", rpt_years, key="rpt_y")
        
        rpt_data_y = dash_df[dash_df['Year'] == rpt_sel_year]
        rpt_months = sorted(rpt_data_y['月份'].unique().tolist(), reverse=True)
        with c_rpt_m: rpt_sel_month = st.selectbox("報表月份", ["整年度"] + rpt_months, key="rpt_m")
        
        if st.button("📥 下載 PDF 報表檔案"):
            rpt_df = rpt_data_y.copy()
            if rpt_sel_month != "整年度": rpt_df = rpt_df[rpt_df['月份'] == rpt_sel_month]
            
            pdf_data = generate_pdf_report(rpt_df, global_project, rpt_sel_year, rpt_sel_month)
            file_name = f"財務報表_{global_project}_{rpt_sel_year}_{rpt_sel_month}.pdf"
            st.download_button(label="📥 點此下載 PDF", data=pdf_data, file_name=file_name, mime="application/pdf")

# --- Tab 4: 設定與管理 ---
with tab_settings:
    st.info("⚠️ 雲端版設定管理功能已簡化。若需新增專案或修改類別，請直接修改 `finance_settings.json` 並推送到 GitHub，或是在 Google Sheets 中操作。")
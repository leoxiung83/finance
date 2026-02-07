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
st.set_page_config(page_title="勁翔營造 工地計帳系統 (線上完整版)", layout="wide", page_icon="🏗️")

# --- 常數設定 ---
# 字型設定 (使用您上傳的 kaiu.ttf)
FONT_FILE = 'kaiu.ttf' 
FONT_NAME = 'Kaiu'

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
# 1. Google Sheets 核心連線與 I/O
# ==========================================

def get_google_sheet_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.session_state.get('gcp_service_account', st.secrets["gcp_service_account"]))
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

@st.cache_data(ttl=10) # 設定短快取，確保資料即時性但不過度請求
def load_data_from_gsheet():
    try:
        client = get_google_sheet_client()
        # 讀取第一張工作表作為資料庫
        sheet = client.open("FinanceData").sheet1 
        data = sheet.get_all_records()
        
        cols = ['日期', '專案', '類別', '項目內容', '單位', '數量', '單價', '總價',
                '購買地點', '經手人', '憑證類型', '發票號碼', '備註']
        
        if not data:
            return pd.DataFrame(columns=cols)

        df = pd.DataFrame(data)
        
        # 確保所有必要欄位都存在
        for c in cols:
            if c not in df.columns:
                df[c] = ""

        # 格式化處理
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
        st.error(f"讀取 Google Sheets 資料失敗: {e}")
        return pd.DataFrame()

def save_dataframe_to_gsheet(df):
    """
    將完整的 DataFrame 覆寫回 Google Sheets (實現編輯與刪除功能)
    """
    try:
        client = get_google_sheet_client()
        sheet = client.open("FinanceData").sheet1
        
        # 移除輔助欄位再儲存
        cols_to_drop = ['月份', 'Year', 'temp_month', '刪除', '星期/節日']
        df_save = df.drop(columns=[c for c in cols_to_drop if c in df.columns])
        
        # 轉換日期為字串
        df_save['日期'] = df_save['日期'].astype(str)
        
        # 1. 清空工作表
        sheet.clear()
        
        # 2. 寫入新的資料 (包含標題)
        # update 方法需要 list of lists
        sheet.update([df_save.columns.values.tolist()] + df_save.values.tolist())
        
        # 清除快取，強制下次讀取最新
        load_data_from_gsheet.clear()
        return True
    except Exception as e:
        st.error(f"儲存至 Google Sheets 失敗: {e}")
        return False

# --- 設定檔 I/O (改用 Google Sheets 的 'Settings' 工作表) ---
def load_settings_from_gsheet():
    default_settings = {
        "projects": ["預設專案"],
        "items": {"預設專案": {c["key"]: [] for c in DEFAULT_CAT_CONFIG}},
        "locations": {"預設專案": {c["key"]: [] for c in DEFAULT_CAT_CONFIG}},
        "cat_config": DEFAULT_CAT_CONFIG
    }
    
    try:
        client = get_google_sheet_client()
        # 嘗試開啟名為 "Settings" 的工作表
        try:
            ws = client.open("FinanceData").worksheet("Settings")
        except:
            # 如果不存在，可以考慮自動建立或報錯，這裡回傳預設值
            st.warning("找不到 'Settings' 工作表，將使用預設設定。請在試算表中新增名為 'Settings' 的分頁以啟用雲端設定儲存。")
            return default_settings

        # 設定檔存在 A1 儲存格
        json_str = ws.acell('A1').value
        if json_str:
            return json.loads(json_str)
        else:
            return default_settings
            
    except Exception as e:
        # st.error(f"讀取設定失敗: {e}") # 避免干擾畫面，先不報錯
        return default_settings

def save_settings_to_gsheet(settings_data):
    try:
        client = get_google_sheet_client()
        try:
            ws = client.open("FinanceData").worksheet("Settings")
        except:
            st.error("無法儲存設定：找不到 'Settings' 工作表。")
            return

        json_str = json.dumps(settings_data, ensure_ascii=False)
        ws.update('A1', [[json_str]]) # 寫入 A1
        st.toast("⚙️ 設定已同步至雲端")
    except Exception as e:
        st.error(f"儲存設定失敗: {e}")

# ==========================================
# 2. 輔助函式
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

def append_finance_record(date, project, category, item, unit, qty, price, location, handler, r_type, inv_no, note):
    # 這是 Tab 1 用的快速寫入 (Append)，效率較高
    total = qty * price
    inv_no_str = str(inv_no) if inv_no else ""
    row_data = [
        str(date), project, category, item,
        unit, qty, price, total,
        location, handler, r_type, inv_no_str, note
    ]
    try:
        client = get_google_sheet_client()
        sheet = client.open("FinanceData").sheet1
        sheet.append_row(row_data)
        load_data_from_gsheet.clear()
    except Exception as e:
        st.error(f"新增失敗: {e}")

# --- PDF 生成 (使用標楷體) ---
def generate_pdf_report(df, project_name, year, month):
    buffer = io.BytesIO()
    # A4 直式
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.0*cm, leftMargin=1.0*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
    
    # 註冊標楷體
    font_path = FONT_FILE 
    try:
        pdfmetrics.registerFont(TTFont(FONT_NAME, font_path))
        # 標楷體通常沒有粗體檔，直接用同一個字型
        pdfmetrics.registerFont(TTFont(f'{FONT_NAME}-Bold', font_path)) 
        font_main = FONT_NAME
        font_bold = f'{FONT_NAME}-Bold'
    except:
        font_main = 'Helvetica'
        font_bold = 'Helvetica-Bold'
        st.warning(f"⚠️ 找不到 {FONT_FILE}，請確認已上傳至 GitHub。")

    # --- 樣式設定 ---
    accent_color = colors.HexColor('#003366')
    header_bg_color = colors.HexColor('#003366')
    header_text_color = colors.white
    summary_bg = colors.HexColor('#F0F4F8')
    zebra_bg_odd = colors.HexColor('#F9F9F9')
    zebra_bg_even = colors.white

    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(name='Title_TC', parent=styles['Heading1'], fontName=font_bold, fontSize=24, leading=32, alignment=1, textColor=accent_color, spaceAfter=6)
    style_subtitle = ParagraphStyle(name='SubTitle_TC', parent=styles['Heading2'], fontName=font_main, fontSize=14, leading=18, alignment=1, textColor=colors.black, spaceAfter=20)
    style_h2 = ParagraphStyle(name='H2_TC', parent=styles['Heading2'], fontName=font_bold, fontSize=16, leading=20, spaceBefore=15, spaceAfter=10, textColor=accent_color, keepWithNext=True)
    style_h3 = ParagraphStyle(name='H3_TC', parent=styles['Heading3'], fontName=font_bold, fontSize=12, leading=16, spaceBefore=12, spaceAfter=6, textColor=colors.black, keepWithNext=True)
    style_table_cell = ParagraphStyle(name='TableCell_TC', parent=styles['Normal'], fontName=font_main, fontSize=10, leading=12)

    elements = []
    
    # 標題區
    if month == "整年度": time_display = f"{year}年年報"
    else: m_only = month.split('-')[1]; time_display = f"{year}年{m_only}月份"

    elements.append(Paragraph("勁翔營造工地支出報表", style_title))
    elements.append(Paragraph(time_display, style_subtitle))
    
    info_data = [[f"專案名稱：{project_name}", f"列印時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}"]]
    t_info = Table(info_data, colWidths=[300, 240])
    t_info.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_main), ('FONTSIZE', (0,0), (-1,-1), 10), ('ALIGN', (0,0), (0,0), 'LEFT'), ('ALIGN', (1,0), (1,0), 'RIGHT')]))
    elements.append(t_info)
    elements.append(HRFlowable(width="100%", thickness=2, color=accent_color, spaceBefore=5, spaceAfter=15))

    # 一、財務總覽
    elements.append(Paragraph("一、財務總覽", style_h2))
    rpt_inc = df[df['類別'] == '入帳金額']['總價'].sum()
    rpt_exp = df[df['類別'] != '入帳金額']['總價'].sum()
    rpt_bal = rpt_inc - rpt_exp
    
    data_summary = [['項目', '總入帳', '總支出', '目前結餘'], ['金額', f"${rpt_inc:,.0f}", f"${rpt_exp:,.0f}", f"${rpt_bal:,.0f}"]]
    t_sum = Table(data_summary, colWidths=[100, 140, 140, 140], hAlign='LEFT')
    t_sum.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_main), ('FONTSIZE', (0,0), (-1,-1), 11), ('LEADING', (0,0), (-1,-1), 16), ('BACKGROUND', (0,0), (-1,0), accent_color), ('TEXTCOLOR', (0,0), (-1,0), header_text_color), ('FONTNAME', (0,0), (-1,0), font_bold), ('ALIGN', (0,0), (-1,0), 'CENTER'), ('ALIGN', (1,1), (-1,1), 'RIGHT'), ('BACKGROUND', (0,1), (-1,1), summary_bg), ('GRID', (0,0), (-1,-1), 1, colors.grey), ('TEXTCOLOR', (3,1), (3,1), colors.red if rpt_bal < 0 else accent_color), ('FONTNAME', (3,1), (3,1), font_bold)]))
    elements.append(t_sum)
    elements.append(Spacer(1, 20))
    
    # 二、支出結構分析
    elements.append(Paragraph("二、支出結構分析", style_h2))
    exp_summary = df[df['類別'] != '入帳金額'].groupby('類別')['總價'].sum().reset_index().sort_values('總價', ascending=False)
    if not exp_summary.empty:
        exp_data = [['支出大項', '金額', '佔比']]
        for i, row in exp_summary.iterrows():
            pct = (row['總價'] / rpt_exp * 100) if rpt_exp > 0 else 0
            exp_data.append([row['類別'], f"${row['總價']:,.0f}", f"{pct:.1f}%"])
        t_exp = Table(exp_data, colWidths=[200, 120, 80], hAlign='LEFT')
        zebra_styles = [] 
        for i in range(1, len(exp_data)):
            bg_c = zebra_bg_odd if i % 2 != 0 else zebra_bg_even
            zebra_styles.append(('BACKGROUND', (0, i), (-1, i), bg_c))
        t_exp.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_main), ('FONTSIZE', (0,0), (-1,-1), 10), ('LEADING', (0,0), (-1,-1), 14), ('BACKGROUND', (0,0), (-1,0), header_bg_color), ('TEXTCOLOR', (0,0), (-1,0), header_text_color), ('FONTNAME', (0,0), (-1,0), font_bold), ('ALIGN', (1,0), (-1,-1), 'RIGHT'), ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey), ('BOX', (0,0), (-1,-1), 1, accent_color)] + zebra_styles))
        elements.append(t_exp)
    elements.append(Spacer(1, 20))
    
    # 三、詳細支出表
    elements.append(Paragraph("三、各分類詳細支出表", style_h2))
    cat_order = ['入帳金額'] + exp_summary['類別'].tolist()
    col_widths = [55, 25, 85, 25, 25, 40, 50, 45, 35, 30, 50, 65]
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
            table_data.append([str(row['日期']), w_simple, Paragraph(str(row['項目內容']), style_table_cell), str(row['單位']), f"{row['數量']}", f"{row['單價']:,.0f}", f"{row['總價']:,.0f}", Paragraph(str(row['購買地點']), style_table_cell), str(row['經手人'])[:4], str(row['憑證類型']), str(row['發票號碼']), Paragraph(str(row['備註']), style_table_cell)])
        t_detail = Table(table_data, colWidths=col_widths, repeatRows=1)
        detail_zebra = []
        for i in range(1, len(table_data)):
             bg_c = zebra_bg_odd if i % 2 != 0 else zebra_bg_even
             detail_zebra.append(('BACKGROUND', (0, i), (-1, i), bg_c))
        t_detail.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_main), ('FONTSIZE', (0,0), (-1,-1), 10), ('LEADING', (0,0), (-1,-1), 12), ('BACKGROUND', (0,0), (-1,0), header_bg_color), ('TEXTCOLOR', (0,0), (-1,0), header_text_color), ('FONTNAME', (0,0), (-1,0), font_bold), ('ALIGN', (0,0), (-1,-1), 'LEFT'), ('ALIGN', (4,0), (6,-1), 'RIGHT'), ('VALIGN', (0,0), (-1,-1), 'TOP'), ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey), ('BOX', (0,0), (-1,-1), 1, accent_color)] + detail_zebra))
        elements.append(t_detail)
        elements.append(Spacer(1, 20))
        
    doc.build(elements)
    buffer.seek(0)
    return buffer

# ==========================================
# 3. UI 介面
# ==========================================

settings = load_settings_from_gsheet()
df = load_data_from_gsheet()

st.title("🏗️ 勁翔營造 工地計帳系統 (線上完整版)")

if 'last_check_date' not in st.session_state:
    st.session_state.last_check_date = datetime.now().date()

with st.sidebar:
    st.header("📅 專案選擇")
    if not settings["projects"]: settings["projects"] = ["預設專案"]
    
    # 確保專案存在
    current_proj_idx = 0
    if "global_project" in st.session_state and st.session_state.global_project in settings["projects"]:
        current_proj_idx = settings["projects"].index(st.session_state.global_project)
    
    global_project = st.selectbox("目前專案", settings["projects"], index=current_proj_idx)
    st.session_state.global_project = global_project # 同步狀態

    global_date = st.date_input("記帳日期", st.session_state.last_check_date)
    
    if global_date != st.session_state.last_check_date:
        st.session_state.last_check_date = global_date
        components.html("""<script>var tabs=window.parent.document.querySelectorAll('[data-testid="stTab"]');if(tabs.length>0){tabs[0].click();}</script>""", height=0, width=0)

    day_str, is_red = get_date_info(global_date)
    if is_red: st.markdown(f"<h3 style='color: #FF4B4B;'>{global_date} {day_str}</h3>", unsafe_allow_html=True)
    else: st.markdown(f"### {global_date} {day_str}")

tab_entry, tab_data, tab_dash, tab_settings = st.tabs(["📝 支出填寫", "📋 明細管理", "📊 收支儀表板", "⚙️ 設定與管理"])

# --- Tab 1: 支出填寫 ---
with tab_entry:
    st.info(f"📍 當前專案：{global_project} | 日期：{global_date} {day_str}")
    
    # 儲存按鈕處理 (改為呼叫 gsheet 版)
    def handle_save_tab1(conf_key, conf_type, display_name):
        k_sel = f"sel_{conf_key}"; k_man = f"man_{conf_key}"; k_sel_loc = f"sel_loc_{conf_key}"
        k_man_loc = f"man_loc_{conf_key}"; k_loc = f"loc_{conf_key}"
        k_buyer = f"buyer_{conf_key}"; k_type = f"type_{conf_key}"
        k_inv = f"inv_{conf_key}"; k_qty = f"qty_{conf_key}"
        k_unit = f"unit_{conf_key}"; k_price = f"price_{conf_key}"
        k_note = f"note_{conf_key}"

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
            st.toast(f"❌ 請輸入 {display_name} 的項目/來源！", icon="⚠️"); return

        append_finance_record(global_date, global_project, conf_key, final_item, unit, qty, price, location, handler, r_type, inv_no, note)
        st.toast(f"✅ {display_name} 儲存成功！")
        
        # 清空
        st.session_state[k_man] = ""; st.session_state[k_price] = 0; st.session_state[k_note] = ""; st.session_state[k_buyer] = ""
        if conf_type != "income": st.session_state[k_man_loc] = ""; st.session_state[k_inv] = ""; st.session_state[k_qty] = 1.0

    for conf in settings["cat_config"]:
        icon = "💰" if conf["type"] == "income" else "💸"
        k_sel = f"sel_{conf['key']}"; k_man = f"man_{conf['key']}"; k_price = f"price_{conf['key']}"
        k_buyer = f"buyer_{conf['key']}"; k_note = f"note_{conf['key']}"; k_sel_loc = f"sel_loc_{conf['key']}"
        k_man_loc = f"man_loc_{conf['key']}"; k_type = f"type_{conf['key']}"; k_inv = f"inv_{conf['key']}"
        k_qty = f"qty_{conf['key']}"; k_unit = f"unit_{conf['key']}"

        if k_man not in st.session_state: st.session_state[k_man] = ""
        if k_price not in st.session_state: st.session_state[k_price] = 0
        if k_qty not in st.session_state: st.session_state[k_qty] = 1.0
        
        with st.expander(f"{icon} {conf['display']}", expanded=False):
            col1, col2 = st.columns(2)
            items_list = settings["items"].get(global_project, {}).get(conf["key"], [])
            items_with_manual = items_list + ["✏️ 手動輸入..."]
            
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
                    locs_list = settings["locations"].get(global_project, {}).get(conf["key"], [])
                    locs_with_manual = locs_list + ["✏️ 手動輸入..."]
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
            
            st.button("💾 儲存紀錄", key=f"btn_save_{conf['key']}", on_click=handle_save_tab1, args=(conf['key'], conf['type'], conf['display']))

# --- Tab 2: 明細管理 (完整恢復編輯與刪除功能) ---
with tab_data:
    proj_df = df[df['專案'] == global_project].copy()
    if proj_df.empty:
        st.info("⚠️ 本專案尚無任何資料")
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
            view_df = view_df[view_df['項目內容'].str.contains(search_kw, case=False) | view_df['備註'].str.contains(search_kw, case=False) | view_df['發票號碼'].str.contains(search_kw, case=False)]

        st.divider()
        if view_df.empty:
            st.warning("查無符合條件的資料")
        else:
            for conf in settings["cat_config"]:
                # 篩選該類別資料
                cat_df = view_df[view_df['類別'] == conf['key']].copy()
                cat_df['總價'] = pd.to_numeric(cat_df['總價'], errors='coerce').fillna(0)
                subtotal = cat_df['總價'].sum()
                count = len(cat_df)
                
                if count > 0:
                    st.markdown(f"### {conf['display']}")
                    st.caption(f"筆數: {count} | 小計: ${subtotal:,.0f}")
                    
                    # 準備編輯器資料
                    cat_df['刪除'] = False
                    cat_df['星期/節日'] = cat_df['日期'].apply(lambda x: get_date_info(x)[0])
                    cols_to_show = ['刪除', '日期', '星期/節日', '項目內容', '單位', '數量', '單價', '總價', '購買地點', '經手人', '憑證類型', '發票號碼', '備註']
                    cols_to_show = [c for c in cols_to_show if c in cat_df.columns]
                    cat_df = cat_df[cols_to_show]

                    # 設定欄位組態
                    if conf['type'] == 'income':
                        col_config = {
                            "刪除": st.column_config.CheckboxColumn(width="small"),
                            "總價": st.column_config.NumberColumn(format="$%d", disabled=True),
                            "日期": st.column_config.DateColumn(format="YYYY-MM-DD", width="small"),
                            "星期/節日": st.column_config.TextColumn(disabled=True, width="small"),
                            "項目內容": st.column_config.TextColumn("入帳來源"),
                            "購買地點": None, "憑證類型": None, "發票號碼": None, "數量": None, "單位": None
                        }
                    else:
                        col_config = {
                            "刪除": st.column_config.CheckboxColumn(width="small"),
                            "總價": st.column_config.NumberColumn(format="$%d", disabled=True),
                            "日期": st.column_config.DateColumn(format="YYYY-MM-DD", width="small"),
                            "星期/節日": st.column_config.TextColumn(disabled=True, width="small"),
                        }

                    edited_cat = st.data_editor(
                        cat_df.sort_values('日期', ascending=False),
                        column_config=col_config,
                        use_container_width=True,
                        num_rows="dynamic",
                        key=f"editor_{conf['key']}_{sel_year}_{sel_month}",
                        hide_index=True
                    )
                    
                    # 更新與刪除按鈕
                    c_btn1, c_btn2, _ = st.columns([1, 1, 4])
                    if c_btn1.button("💾 更新修改", key=f"btn_upd_{conf['key']}"):
                        if search_kw:
                            st.error("⚠️ 搜尋模式下無法存檔！請先清除搜尋關鍵字。")
                        else:
                            with st.spinner("正在同步至 Google Sheets..."):
                                final_df = edited_cat.copy()
                                final_df['數量'] = pd.to_numeric(final_df['數量'], errors='coerce').fillna(0)
                                final_df['單價'] = pd.to_numeric(final_df['單價'], errors='coerce').fillna(0)
                                final_df['總價'] = final_df['數量'] * final_df['單價']
                                final_df['類別'] = conf['key']
                                final_df['專案'] = global_project
                                
                                # 邏輯：從原始 df 中移除「舊的這群資料」，然後補上「新的這群資料」
                                # 1. 找出要保留的資料 (即不屬於當前篩選範圍的資料)
                                current_full_df = df # 這是全域載入的 df
                                
                                # 建立篩選遮罩 (找出當前編輯區塊對應的原始資料)
                                mask_target = (
                                    (current_full_df['專案'] == global_project) & 
                                    (current_full_df['類別'] == conf['key']) &
                                    (current_full_df['Year'] == sel_year)
                                )
                                if sel_month != "整年":
                                    mask_target = mask_target & (current_full_df['月份'] == sel_month)
                                
                                df_kept = current_full_df[~mask_target] # 保留其他資料
                                
                                # 2. 處理編輯後的資料 (移除刪除勾選者、計算欄位)
                                df_to_add = final_df.drop(columns=['刪除', '星期/節日'], errors='ignore')
                                
                                # 3. 合併並上傳
                                full_new_df = pd.concat([df_kept, df_to_add], ignore_index=True)
                                if save_dataframe_to_gsheet(full_new_df):
                                    st.success("✅ 更新成功！")
                                    time.sleep(1)
                                    st.rerun()

                    if c_btn2.button("🗑️ 刪除選取", key=f"btn_del_{conf['key']}"):
                        if not edited_cat['刪除'].any():
                            st.warning("請先勾選表格內的「刪除」框框")
                        elif search_kw:
                            st.error("搜尋模式下無法執行刪除")
                        else:
                            with st.spinner("正在執行刪除..."):
                                rows_to_keep = edited_cat[edited_cat['刪除'] == False].copy()
                                
                                # 同樣邏輯：保留非目標群 + 新的目標群(已過濾掉刪除者)
                                current_full_df = df
                                mask_target = (
                                    (current_full_df['專案'] == global_project) & 
                                    (current_full_df['類別'] == conf['key']) &
                                    (current_full_df['Year'] == sel_year)
                                )
                                if sel_month != "整年":
                                    mask_target = mask_target & (current_full_df['月份'] == sel_month)
                                
                                df_kept = current_full_df[~mask_target]
                                df_to_add = rows_to_keep.drop(columns=['刪除', '星期/節日'], errors='ignore')
                                df_to_add['類別'] = conf['key']; df_to_add['專案'] = global_project
                                df_to_add['數量'] = pd.to_numeric(df_to_add['數量'], errors='coerce').fillna(0)
                                df_to_add['單價'] = pd.to_numeric(df_to_add['單價'], errors='coerce').fillna(0)
                                df_to_add['總價'] = df_to_add['數量'] * df_to_add['單價']

                                full_new_df = pd.concat([df_kept, df_to_add], ignore_index=True)
                                if save_dataframe_to_gsheet(full_new_df):
                                    st.success("已刪除選取項目")
                                    time.sleep(1)
                                    st.rerun()
                    st.markdown("---")

# --- Tab 3: 收支儀表板 ---
with tab_dash:
    dash_df = df[df['專案'] == global_project].copy()
    if not dash_df.empty:
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
        
        # 簡單圖表
        chart_df = expense_df.groupby('類別')['總價'].sum().reset_index()
        if not chart_df.empty:
            c = alt.Chart(chart_df).mark_arc(innerRadius=50).encode(
                theta=alt.Theta("總價", stack=True), color="類別", tooltip=["類別", "總價"]
            )
            st.altair_chart(c, use_container_width=True)

    # --- PDF 報表 ---
    st.divider()
    st.subheader("📄 產出財務報表")
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
            st.download_button(label="📥 點此下載 PDF (標楷體)", data=pdf_data, file_name=file_name, mime="application/pdf")

# --- Tab 4: 設定與管理 (恢復完整功能，並同步至雲端) ---
with tab_settings:
    st.header("⚙️ 設定與管理")
    st.markdown("### 一、專案管理")
    
    # 專案管理 (新增/改名/刪除)
    with st.expander("專案管理 (新增/改名/刪除)", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            new_proj = st.text_input("新增專案名稱")
            if st.button("➕ 新增專案"):
                if new_proj and new_proj not in settings["projects"]:
                    settings["projects"].append(new_proj)
                    # 初始化新專案的選單
                    settings["items"][new_proj] = {c["key"]: [] for c in settings["cat_config"]}
                    settings["locations"][new_proj] = {c["key"]: [] for c in settings["cat_config"]}
                    save_settings_to_gsheet(settings); time.sleep(1); st.rerun()
            
            st.divider()
            rename_proj = st.text_input("修改目前專案名稱", value=global_project)
            if st.button("✏️ 確認改名"):
                if rename_proj and rename_proj != global_project:
                    # 更新設定檔
                    settings["projects"] = [rename_proj if p == global_project else p for p in settings["projects"]]
                    settings["items"][rename_proj] = settings["items"].pop(global_project)
                    settings["locations"][rename_proj] = settings["locations"].pop(global_project)
                    save_settings_to_gsheet(settings)
                    
                    # 更新資料庫中的專案名稱 (比較耗時)
                    with st.spinner("正在更新所有歷史資料..."):
                        if not df.empty:
                            df.loc[df['專案'] == global_project, '專案'] = rename_proj
                            save_dataframe_to_gsheet(df)
                    st.success(f"專案已改名為：{rename_proj}"); time.sleep(1); st.rerun()

        with c2:
            st.info(f"正在管理專案：{global_project}")
            if "del_proj_confirm" not in st.session_state: st.session_state.del_proj_confirm = False
            if not st.session_state.del_proj_confirm:
                if st.button("🗑️ 刪除此專案"):
                    if len(settings["projects"]) <= 1: st.error("這是最後一個專案，無法刪除！")
                    else: st.session_state.del_proj_confirm = True; st.rerun()
            else:
                st.warning(f"⚠️ 確定要刪除「{global_project}」嗎？此動作無法復原！")
                if st.button("✔️ 是，刪除"):
                    with st.spinner("正在刪除專案資料..."):
                        settings["projects"].remove(global_project)
                        del settings["items"][global_project]
                        del settings["locations"][global_project]
                        save_settings_to_gsheet(settings)
                        
                        if not df.empty:
                            new_df = df[df['專案'] != global_project]
                            save_dataframe_to_gsheet(new_df)
                    st.session_state.del_proj_confirm = False; st.success("專案已刪除"); time.sleep(1); st.rerun()

    # 細項選單管理
    st.markdown("### 二、選單管理")
    with st.expander("細項選單管理 (修改標題/新增/刪除)", expanded=True):
        target_cat = st.selectbox("選擇要管理的大項", [c["display"] for c in settings["cat_config"]])
        cat_key = next(c["key"] for c in settings["cat_config"] if c["display"] == target_cat)
        cat_type = next(c["type"] for c in settings["cat_config"] if c["display"] == target_cat)
        
        # 確保結構存在
        if global_project not in settings["items"]: settings["items"][global_project] = {c["key"]: [] for c in settings["cat_config"]}
        if cat_key not in settings["items"][global_project]: settings["items"][global_project][cat_key] = []
        if global_project not in settings["locations"]: settings["locations"][global_project] = {c["key"]: [] for c in settings["cat_config"]}
        
        if cat_type == "income":
            list_type = "item"; current_list = settings["items"][global_project][cat_key]
            st.markdown(f"**管理【{target_cat}】的入帳來源**")
        else:
            mode_sel = st.radio("選擇要管理的清單", ["📦 購買內容 (Items)", "📍 購買地點 (Locations)"], horizontal=True)
            if "內容" in mode_sel:
                list_type = "item"; current_list = settings["items"][global_project][cat_key]
            else:
                list_type = "location"; current_list = settings["locations"][global_project][cat_key]
        
        c_add1, c_add2 = st.columns([4, 1])
        with c_add1: new_item = st.text_input("輸入名稱新增", key="new_item_input")
        with c_add2:
            if st.button("➕ 加入"):
                if new_item and new_item not in current_list:
                    if list_type == "item": settings["items"][global_project][cat_key].append(new_item)
                    else: settings["locations"][global_project][cat_key].append(new_item)
                    save_settings_to_gsheet(settings); st.success("已加入"); st.rerun()
        
        if current_list:
            st.markdown("#### 現有項目")
            for i, item in enumerate(current_list):
                c1, c2 = st.columns([4, 1])
                with c1: st.text(item)
                with c2:
                    if st.button("🗑️", key=f"del_{list_type}_{i}"):
                        if list_type == "item": settings["items"][global_project][cat_key].remove(item)
                        else: settings["locations"][global_project][cat_key].remove(item)
                        save_settings_to_gsheet(settings); st.rerun()
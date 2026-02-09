import streamlit as st
import pandas as pd
import os
import json
import time
import altair as alt
import streamlit.components.v1 as components
from datetime import datetime
import zipfile
import io
import copy

# --- 1. 安全匯入機制 ---
try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    HAS_GOOGLE_LIB = True
except ImportError:
    HAS_GOOGLE_LIB = False

try:
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.units import cm
    HAS_PDF_LIB = True
except ImportError:
    HAS_PDF_LIB = False

# 設定頁面
st.set_page_config(page_title="勁翔營造 工地記帳系統 ( 線上版 ) ", layout="wide", page_icon="🏗️")

# --- 檔案與字型設定 ---
DATA_FILE = 'finance_data.csv'
SETTINGS_FILE = 'finance_settings.json'
FONT_FILE = 'kaiu.ttf' 
FONT_NAME = 'Kaiu'

# --- 判斷執行模式 ---
def check_mode():
    if not HAS_GOOGLE_LIB: return "local"
    try:
        if "gcp_service_account" in st.secrets: return "cloud"
    except: pass
    return "local"

MODE = check_mode()

# --- 台灣例假日 ---
HOLIDAYS = {
    "2025-01-01": "元旦", "2025-01-27": "小年夜", "2025-01-28": "除夕", "2025-01-29": "春節", "2025-01-30": "初二", "2025-01-31": "初三",
    "2025-02-28": "和平紀念日", "2025-04-04": "兒童節/清明節", "2025-05-01": "勞動節", "2025-05-31": "端午節",
    "2025-10-06": "中秋節", "2025-10-10": "國慶日",
    "2026-01-01": "元旦", "2026-02-16": "小年夜", "2026-02-17": "除夕", "2026-02-18": "春節",
    "2026-02-28": "和平紀念日", "2026-04-04": "兒童節", "2026-04-05": "清明節", "2026-05-01": "勞動節",
    "2026-06-19": "端午節", "2026-09-25": "中秋節", "2026-10-10": "國慶日"
}

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
# 1. 資料存取層 (Backend)
# ==========================================

@st.cache_resource
def get_gsheet_client():
    if not HAS_GOOGLE_LIB: return None
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    except:
        return None

@st.cache_data(ttl=60)
def load_data():
    cols = ['日期', '專案', '類別', '項目內容', '單位', '數量', '單價', '總價', '購買地點', '經手人', '憑證類型', '發票號碼', '備註', '月份', 'Year']
    
    if MODE == "cloud":
        try:
            client = get_gsheet_client()
            if client:
                sheet = client.open("FinanceData").sheet1
                data = sheet.get_all_records()
                df = pd.DataFrame(data) if data else pd.DataFrame(columns=cols)
                for c in cols:
                    if c not in df.columns: df[c] = ""
                # 格式化
                text_cols = ['發票號碼', '備註', '購買地點', '經手人', '項目內容', '專案', '類別', '單位', '憑證類型']
                for col in text_cols:
                    if col in df.columns: df[col] = df[col].fillna("").astype(str)
                if '日期' in df.columns:
                    df['日期'] = pd.to_datetime(df['日期']).dt.date
                    df['月份'] = pd.to_datetime(df['日期']).dt.strftime("%Y-%m")
                    df['Year'] = pd.to_datetime(df['日期']).dt.year
                return df
        except:
            pass 
            
    # Local Mode
    if os.path.exists(DATA_FILE):
        try:
            df = pd.read_csv(DATA_FILE)
        except:
            df = pd.DataFrame(columns=cols)
    else:
        df = pd.DataFrame(columns=cols)
        df.to_csv(DATA_FILE, index=False, encoding='utf-8-sig')

    text_cols = ['發票號碼', '備註', '購買地點', '經手人', '項目內容', '專案', '類別', '單位', '憑證類型']
    for col in text_cols:
        if col in df.columns: df[col] = df[col].fillna("").astype(str)
    if '日期' in df.columns:
        df['日期'] = pd.to_datetime(df['日期']).dt.date
        df['月份'] = pd.to_datetime(df['日期']).dt.strftime("%Y-%m")
        df['Year'] = pd.to_datetime(df['日期']).dt.year
    return df

def save_dataframe(df):
    try:
        cols_to_drop = ['月份', 'Year', 'temp_month', '刪除', '星期/節日']
        df_save = df.drop(columns=[c for c in cols_to_drop if c in df.columns])
        
        if MODE == "cloud":
            client = get_gsheet_client()
            if client:
                sheet = client.open("FinanceData").sheet1
                df_save['日期'] = df_save['日期'].astype(str)
                sheet.clear()
                sheet.update([df_save.columns.values.tolist()] + df_save.values.tolist())
                load_data.clear()
                return True
        else:
            df_save.to_csv(DATA_FILE, index=False, encoding='utf-8-sig')
            return True
    except Exception as e:
        st.error(f"儲存失敗: {e}")
        return False

def load_settings():
    default = {
        "projects": ["預設專案"],
        "items": {"預設專案": {c["key"]: [] for c in DEFAULT_CAT_CONFIG}},
        "locations": {"預設專案": {c["key"]: [] for c in DEFAULT_CAT_CONFIG}},
        "cat_config": DEFAULT_CAT_CONFIG, # 舊格式可能長這樣
        "item_details": {}
    }
    settings = default
    if MODE == "cloud":
        try:
            client = get_gsheet_client()
            if client:
                ws = client.open("FinanceData").worksheet("Settings")
                json_str = ws.acell('A1').value
                if json_str: settings = json.loads(json_str)
        except: pass
    else:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                settings = json.load(f)
    
    # --- 關鍵修正：結構自動遷移邏輯 ---
    # 1. 確保 projects 存在
    if "projects" not in settings: settings["projects"] = ["預設專案"]
    
    # 2. 檢測 cat_config 是否為舊版 List 格式，如果是，轉為新版 Dict 格式
    if isinstance(settings.get("cat_config"), list):
        old_config_list = settings["cat_config"]
        settings["cat_config"] = {} # 重置為字典
        for p in settings["projects"]:
            settings["cat_config"][p] = copy.deepcopy(old_config_list)
            
    # 3. 確保每個專案都有獨立的 cat_config
    if isinstance(settings.get("cat_config"), dict):
        for p in settings["projects"]:
            if p not in settings["cat_config"]:
                # 如果某個專案沒有設定，給它預設值
                settings["cat_config"][p] = copy.deepcopy(DEFAULT_CAT_CONFIG)
    else:
        # 如果 cat_config 既不是 list 也不是 dict (異常情況)，重置
        settings["cat_config"] = {}
        for p in settings["projects"]:
            settings["cat_config"][p] = copy.deepcopy(DEFAULT_CAT_CONFIG)

    if "item_details" not in settings: settings["item_details"] = {}
    return settings

def save_settings(data):
    if MODE == "cloud":
        try:
            client = get_gsheet_client()
            if client:
                ws = client.open("FinanceData").worksheet("Settings")
                ws.update('A1', [[json.dumps(data, ensure_ascii=False)]])
        except: pass
    else:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

def append_record(record_dict):
    if MODE == "cloud":
        try:
            client = get_gsheet_client()
            if client:
                sheet = client.open("FinanceData").sheet1
                row = [
                    str(record_dict['日期']), record_dict['專案'], record_dict['類別'], record_dict['項目內容'],
                    record_dict['單位'], record_dict['數量'], record_dict['單價'], record_dict['總價'],
                    record_dict['購買地點'], record_dict['經手人'], record_dict['憑證類型'],
                    str(record_dict['發票號碼']), record_dict['備註']
                ]
                sheet.append_row(row)
                load_data.clear() 
                return True
        except Exception as e:
            st.error(f"雲端寫入錯誤: {e}")
            return False
    else:
        current_df = load_data()
        new_df = pd.DataFrame([record_dict])
        updated_df = pd.concat([current_df, new_df], ignore_index=True)
        return save_dataframe(updated_df)

def create_zip_backup(target_project=None):
    df_latest = load_data()
    settings_latest = load_settings()
    
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        if target_project and target_project != "所有專案" and target_project != "所有專案 (完整系統)":
            df_out = df_latest[df_latest['專案'] == target_project] if not df_latest.empty else df_latest
            
            # 備份單一專案時，將該專案的設定轉為通用格式，方便還原
            proj_conf = settings_latest.get("cat_config", {}).get(target_project, DEFAULT_CAT_CONFIG)
            
            s_out = {
                "projects": [target_project],
                "cat_config": proj_conf, # 這裡存成 List，讓還原邏輯能識別
                "items": {target_project: settings_latest.get("items", {}).get(target_project, {})},
                "locations": {target_project: settings_latest.get("locations", {}).get(target_project, {})},
                "item_details": {target_project: settings_latest.get("item_details", {}).get(target_project, {})}
            }
        else:
            df_out = df_latest
            s_out = settings_latest # 完整備份直接存 Dict 結構
        
        csv_buffer = io.StringIO()
        df_out.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
        zip_file.writestr('finance_data.csv', csv_buffer.getvalue())
        zip_file.writestr('finance_settings.json', json.dumps(s_out, ensure_ascii=False, indent=4))
    buffer.seek(0)
    return buffer

def get_date_info(date_obj):
    if isinstance(date_obj, str):
        try: date_obj = datetime.strptime(date_obj, "%Y-%m-%d").date()
        except: return "", False
    weekdays = ["(週一)", "(週二)", "(週三)", "(週四)", "(週五)", "(週六)", "(週日)"]
    w_str = weekdays[date_obj.weekday()]
    date_str = date_obj.strftime("%Y-%m-%d")
    is_weekend = date_obj.weekday() >= 5
    if date_str in HOLIDAYS: return f"🔴 {w_str} ★{HOLIDAYS[date_str]}", True 
    if is_weekend: return f"🔴 {w_str}", True 
    return f"{w_str}", False

# --- PDF 生成 ---
def generate_pdf_report(df, project_name, year, month):
    if not HAS_PDF_LIB:
        st.error("系統缺少 'reportlab' 套件。")
        return None
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), rightMargin=1.5*cm, leftMargin=1.5*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
    font_path = FONT_FILE 
    if not os.path.exists(font_path):
        font_main = 'Helvetica'; font_bold = 'Helvetica-Bold'
        st.toast(f"⚠️ 找不到 {FONT_FILE}，使用預設字型。")
    else:
        try:
            pdfmetrics.registerFont(TTFont(FONT_NAME, font_path))
            pdfmetrics.registerFont(TTFont(f'{FONT_NAME}-Bold', font_path)) 
            font_main = FONT_NAME; font_bold = f'{FONT_NAME}-Bold'
        except:
            font_main = 'Helvetica'; font_bold = 'Helvetica-Bold'

    accent_color = colors.HexColor('#003366'); header_bg_color = colors.HexColor('#003366')
    header_text_color = colors.white; summary_bg = colors.HexColor('#F0F4F8')
    zebra_bg_odd = colors.HexColor('#F9F9F9'); zebra_bg_even = colors.white

    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(name='Title_TC', parent=styles['Heading1'], fontName=font_bold, fontSize=28, leading=36, alignment=1, textColor=accent_color, spaceAfter=6)
    style_subtitle = ParagraphStyle(name='SubTitle_TC', parent=styles['Heading2'], fontName=font_main, fontSize=16, leading=20, alignment=1, textColor=colors.black, spaceAfter=20)
    style_h2 = ParagraphStyle(name='H2_TC', parent=styles['Heading2'], fontName=font_bold, fontSize=18, leading=22, spaceBefore=15, spaceAfter=10, textColor=accent_color, keepWithNext=True)
    style_h3 = ParagraphStyle(name='H3_TC', parent=styles['Heading3'], fontName=font_bold, fontSize=14, leading=18, spaceBefore=12, spaceAfter=6, textColor=colors.black, keepWithNext=True)
    style_table_cell = ParagraphStyle(name='TableCell_TC', parent=styles['Normal'], fontName=font_main, fontSize=11, leading=13)

    elements = []
    if month == "整年度": time_display = f"{year}年年報"
    else: m_only = month.split('-')[1]; time_display = f"{year}年{m_only}月份"

    elements.append(Paragraph("勁翔營造工地支出報表", style_title))
    elements.append(Paragraph(time_display, style_subtitle))
    
    info_data = [[f"專案名稱：{project_name}", f"列印時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}"]]
    t_info = Table(info_data, colWidths=[400, 300])
    t_info.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_main), ('FONTSIZE', (0,0), (-1,-1), 11), ('ALIGN', (0,0), (0,0), 'LEFT'), ('ALIGN', (1,0), (1,0), 'RIGHT')]))
    elements.append(t_info)
    elements.append(HRFlowable(width="100%", thickness=2, color=accent_color, spaceBefore=5, spaceAfter=15))

    elements.append(Paragraph("一、財務總覽", style_h2))
    rpt_inc = df[df['類別'] == '入帳金額']['總價'].sum()
    rpt_exp = df[df['類別'] != '入帳金額']['總價'].sum()
    rpt_bal = rpt_inc - rpt_exp
    data_summary = [['項目', '總入帳', '總支出', '目前結餘'], ['金額', f"${rpt_inc:,.0f}", f"${rpt_exp:,.0f}", f"${rpt_bal:,.0f}"]]
    t_sum = Table(data_summary, colWidths=[120, 180, 180, 180], hAlign='LEFT')
    t_sum.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_main), ('FONTSIZE', (0,0), (-1,-1), 12), ('LEADING', (0,0), (-1,-1), 18), ('BACKGROUND', (0,0), (-1,0), accent_color), ('TEXTCOLOR', (0,0), (-1,0), header_text_color), ('FONTNAME', (0,0), (-1,0), font_bold), ('ALIGN', (0,0), (-1,0), 'CENTER'), ('ALIGN', (1,1), (-1,1), 'RIGHT'), ('BACKGROUND', (0,1), (-1,1), summary_bg), ('GRID', (0,0), (-1,-1), 1, colors.grey), ('TEXTCOLOR', (3,1), (3,1), colors.red if rpt_bal < 0 else accent_color), ('FONTNAME', (3,1), (3,1), font_bold)]))
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
        zebra_styles = [('BACKGROUND', (0, i), (-1, i), zebra_bg_odd if i % 2 != 0 else zebra_bg_even) for i in range(1, len(exp_data))]
        t_exp.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_main), ('FONTSIZE', (0,0), (-1,-1), 11), ('LEADING', (0,0), (-1,-1), 14), ('BACKGROUND', (0,0), (-1,0), header_bg_color), ('TEXTCOLOR', (0,0), (-1,0), header_text_color), ('FONTNAME', (0,0), (-1,0), font_bold), ('ALIGN', (1,0), (-1,-1), 'RIGHT'), ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey), ('BOX', (0,0), (-1,-1), 1, accent_color)] + zebra_styles))
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
        detail_zebra = [('BACKGROUND', (0, i), (-1, i), zebra_bg_odd if i % 2 != 0 else zebra_bg_even) for i in range(1, len(table_data))]
        t_detail.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_main), ('FONTSIZE', (0,0), (-1,-1), 11), ('LEADING', (0,0), (-1,-1), 14), ('BACKGROUND', (0,0), (-1,0), header_bg_color), ('TEXTCOLOR', (0,0), (-1,0), header_text_color), ('FONTNAME', (0,0), (-1,0), font_bold), ('ALIGN', (0,0), (-1,-1), 'LEFT'), ('ALIGN', (4,0), (6,-1), 'RIGHT'), ('VALIGN', (0,0), (-1,-1), 'TOP'), ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey), ('BOX', (0,0), (-1,-1), 1, accent_color)] + detail_zebra))
        elements.append(t_detail)
        elements.append(Spacer(1, 25))
    doc.build(elements)
    buffer.seek(0)
    return buffer

# ==========================================
# 3. UI 介面
# ==========================================

settings = load_settings()
df = load_data()

st.title("🏗️ 勁翔營造 工地記帳系統(線上版)")

if 'last_check_date' not in st.session_state:
    st.session_state.last_check_date = datetime.now().date()

with st.sidebar:
    st.header("📅 專案選擇")
    if not settings["projects"]: settings["projects"] = ["預設專案"]
    current_proj_idx = 0
    if "global_project" in st.session_state and st.session_state.global_project in settings["projects"]:
        current_proj_idx = settings["projects"].index(st.session_state.global_project)
    
    global_project = st.selectbox("目前專案", settings["projects"], index=current_proj_idx)
    st.session_state.global_project = global_project

    global_date = st.date_input("記帳日期", st.session_state.last_check_date)
    if global_date != st.session_state.last_check_date:
        st.session_state.last_check_date = global_date
        components.html("""<script>var tabs=window.parent.document.querySelectorAll('[data-testid="stTab"]');if(tabs.length>0){tabs[0].click();}</script>""", height=0, width=0)
    day_str, is_red = get_date_info(global_date)
    if is_red: st.markdown(f"<h3 style='color: #FF4B4B;'>{global_date} {day_str}</h3>", unsafe_allow_html=True)
    else: st.markdown(f"### {global_date} {day_str}")
    
    st.divider()
    if MODE == "local":
        if not HAS_GOOGLE_LIB:
            st.caption("⚠️ 單機模式 (缺少 gspread 套件)")
        elif "gcp_service_account" not in st.secrets:
            st.caption("⚠️ 單機模式 (未偵測到金鑰)")
        else:
            st.caption("💻 單機模式 (連線失敗)")
    else:
        st.caption("✅ 雲端連線正常")
        
    # --- 資料更新按鈕 (位置調整至底部) ---
    st.write("") # Spacer
    st.write("")
    if st.button("🔄 資料更新", use_container_width=True, help="若雲端有更新，請點此同步"):
        load_data.clear()
        st.rerun()

tab_entry, tab_data, tab_dash, tab_settings = st.tabs(["📝 支出填寫", "📋 明細管理", "📊 收支儀表板", "⚙️ 設定與管理"])

# 取得目前專案的獨立設定
current_cat_config = settings["cat_config"][global_project]

# --- Tab 1: 支出填寫 (優化版：選單與手動輸入並存，不自動重整) ---
with tab_entry:
    st.info(f"📍 當前專案：{global_project} | 日期：{global_date} {day_str}")
    
    for conf in current_cat_config: # 使用專案獨立設定
        icon = "💰" if conf["type"] == "income" else "💸"
        
        # 使用 Form 來防止輸入時的頁面重整 (Running Man)
        with st.expander(f"{icon} {conf['display']}", expanded=False):
            with st.form(key=f"form_entry_{conf['key']}"):
                col1, col2 = st.columns(2)
                
                # 準備選單內容
                items_list = settings["items"].get(global_project, {}).get(conf["key"], [])
                
                if conf["type"] == "income":
                    with col1:
                        # 兩個欄位並存：選單 與 手動輸入
                        sel = st.selectbox("入帳來源 (選單)", ["(請選擇)"] + items_list)
                        man_val = st.text_input("或手動輸入來源 (若填寫則優先使用此欄位)")
                        price = st.number_input("入帳金額", min_value=0, step=100)
                    with col2:
                        buyer = st.text_input("收帳人 (經手人)")
                        note = st.text_area("備註", height=100)
                    # 隱藏預設值
                    sel_loc = ""; man_loc = ""; r_type = "無"; inv_no = ""; qty = 1; unit = "次"
                else:
                    with col1:
                        # 兩個欄位並存
                        sel = st.selectbox("項目內容 (選單)", ["(請選擇)"] + items_list)
                        man_val = st.text_input("或手動輸入項目 (若填寫則優先使用此欄位)")
                        
                        locs_list = settings["locations"].get(global_project, {}).get(conf["key"], [])
                        sel_loc = st.selectbox("購買地點 (選單)", ["(請選擇)"] + locs_list)
                        man_loc = st.text_input("或手動輸入地點")
                        
                        buyer = st.text_input("購買人 (經手人)")
                    with col2:
                        r_type = st.radio("憑證類型", ["收據", "發票"], horizontal=True)
                        inv_no = st.text_input("發票號碼")
                        c_q, c_u = st.columns(2)
                        with c_q: qty = st.number_input("數量", min_value=0.0, step=0.5, value=1.0)
                        with c_u: unit = st.text_input("單位", value="式")
                        price = st.number_input("單價/金額", min_value=0, step=1)
                    note = st.text_input("備註")

                # 送出按鈕 (此時才會連線運算)
                submitted = st.form_submit_button("💾 儲存紀錄")
                
                if submitted:
                    # 邏輯判斷：如果有手動輸入，就用手動的；否則用選單的
                    final_item = man_val if man_val.strip() else (sel if sel != "(請選擇)" else "")
                    
                    if conf["type"] != "income":
                        final_loc = man_loc if man_loc.strip() else (sel_loc if sel_loc != "(請選擇)" else "")
                    else:
                        final_loc = ""
                    
                    if not final_item:
                        st.error("❌ 請輸入或選擇項目名稱！")
                    else:
                        record = {
                            '日期': global_date, '專案': global_project, '類別': conf['key'], '項目內容': final_item,
                            '單位': unit, '數量': qty, '單價': price, '總價': qty*price, '購買地點': final_loc,
                            '經手人': buyer, '憑證類型': r_type, '發票號碼': inv_no, '備註': note
                        }
                        with st.spinner("正在儲存..."):
                            if append_record(record):
                                st.toast(f"✅ {conf['display']} 儲存成功！")
                                time.sleep(0.5)

# --- Tab 2: 明細管理 (修正：使用 st.form 包裹 st.data_editor 防止勾選時自動重整) ---
with tab_data:
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
        if search_kw: view_df = view_df[view_df['項目內容'].str.contains(search_kw, case=False) | view_df['備註'].str.contains(search_kw, case=False) | view_df['發票號碼'].str.contains(search_kw, case=False)]
        
        st.divider()
        if view_df.empty: st.warning("查無符合條件的資料")
        else:
            for conf in current_cat_config: # 使用專案獨立設定
                cat_df = view_df[view_df['類別'] == conf['key']].copy()
                cat_df['總價'] = pd.to_numeric(cat_df['總價'], errors='coerce').fillna(0)
                subtotal = cat_df['總價'].sum()
                count = len(cat_df)
                if count > 0:
                    st.markdown(f"### {conf['display']}")
                    st.caption(f"筆數: {count} | 小計: ${subtotal:,.0f}")
                    cat_df['刪除'] = False
                    cat_df['星期/節日'] = cat_df['日期'].apply(lambda x: get_date_info(x)[0])
                    cols_to_show = ['刪除', '日期', '星期/節日', '項目內容', '單位', '數量', '單價', '總價', '購買地點', '經手人', '憑證類型', '發票號碼', '備註']
                    cols_to_show = [c for c in cols_to_show if c in cat_df.columns]
                    cat_df = cat_df[cols_to_show]
                    
                    if conf['type'] == 'income':
                        col_config = {"刪除": st.column_config.CheckboxColumn(width="small"), "總價": st.column_config.NumberColumn(format="$%d", disabled=True), "日期": st.column_config.DateColumn(format="YYYY-MM-DD", width="small"), "星期/節日": st.column_config.TextColumn(disabled=True, width="small"), "項目內容": st.column_config.TextColumn("入帳來源"), "購買地點": None, "憑證類型": None, "發票號碼": None, "數量": None, "單位": None}
                    else:
                        col_config = {"刪除": st.column_config.CheckboxColumn(width="small"), "總價": st.column_config.NumberColumn(format="$%d", disabled=True), "日期": st.column_config.DateColumn(format="YYYY-MM-DD", width="small"), "星期/節日": st.column_config.TextColumn(disabled=True, width="small")}
                    
                    # --- 使用 FORM 包裹表格與按鈕，解決勾選時小人跑動問題 ---
                    with st.form(key=f"form_editor_{conf['key']}"):
                        # 關鍵修正：加入 hide_index=True 並重置索引
                        edited_cat = st.data_editor(cat_df.sort_values('日期', ascending=False).reset_index(drop=True), column_config=col_config, use_container_width=True, num_rows="dynamic", key=f"editor_{conf['key']}_{sel_year}_{sel_month}", hide_index=True)
                        
                        c_btn1, c_btn2, _ = st.columns([1, 1, 4])
                        # 使用 form_submit_button
                        with c_btn1:
                            submit_update = st.form_submit_button("💾 更新修改")
                        with c_btn2:
                            submit_delete = st.form_submit_button("🗑️ 刪除選取")
                    
                    # --- 處理按鈕邏輯 (在 Form 外部處理) ---
                    if submit_update:
                        if search_kw: st.error("搜尋模式下無法存檔！")
                        else:
                            with st.spinner("正在更新資料庫..."):
                                final_df = edited_cat.copy()
                                final_df['數量'] = pd.to_numeric(final_df['數量'], errors='coerce').fillna(0)
                                final_df['單價'] = pd.to_numeric(final_df['單價'], errors='coerce').fillna(0)
                                final_df['總價'] = final_df['數量'] * final_df['單價']
                                final_df['類別'] = conf['key']; final_df['專案'] = global_project
                                current_full_df = df
                                mask = (current_full_df['專案'] == global_project) & (current_full_df['類別'] == conf['key']) & (current_full_df['Year'] == sel_year)
                                if sel_month != "整年": mask = mask & (current_full_df['月份'] == sel_month)
                                df_kept = current_full_df[~mask]
                                df_add = final_df.drop(columns=['刪除', '星期/節日'], errors='ignore')
                                if save_dataframe(pd.concat([df_kept, df_add], ignore_index=True)): st.success("更新成功！"); time.sleep(1); st.rerun()

                    # --- 刪除按鈕邏輯 (檢查勾選並設定 Session State) ---
                    if submit_delete:
                        if not edited_cat['刪除'].any():
                            st.warning("請先勾選要刪除的項目")
                        elif search_kw:
                            st.error("搜尋模式下無法執行刪除")
                        else:
                            # 將要刪除的資料暫存到 Session State，並開啟確認模式
                            st.session_state[f"pending_del_df_{conf['key']}"] = edited_cat
                            st.session_state[f"confirm_del_{conf['key']}"] = True
                            st.rerun() # 強制重整以顯示下方的確認框
                    
                    # --- 顯示確認警告 (在 Form 外部顯示) ---
                    if st.session_state.get(f"confirm_del_{conf['key']}"):
                        st.warning("⚠️ 確定要永久刪除勾選的資料嗎？")
                        col_yes, col_no = st.columns(2)
                        
                        if col_yes.button("✔️ 是，刪除", key=f"yes_{conf['key']}"):
                            # 從 Session State 取回暫存的資料表
                            pending_df = st.session_state.get(f"pending_del_df_{conf['key']}")
                            if pending_df is not None:
                                with st.spinner("正在刪除..."):
                                    rows_keep = pending_df[pending_df['刪除'] == False].copy()
                                    current_full_df = df
                                    mask = (current_full_df['專案'] == global_project) & (current_full_df['類別'] == conf['key']) & (current_full_df['Year'] == sel_year)
                                    if sel_month != "整年": mask = mask & (current_full_df['月份'] == sel_month)
                                    df_kept = current_full_df[~mask]
                                    df_add = rows_keep.drop(columns=['刪除', '星期/節日'], errors='ignore')
                                    df_add['類別'] = conf['key']; df_add['專案'] = global_project
                                    df_add['總價'] = pd.to_numeric(df_add['數量'], errors='coerce') * pd.to_numeric(df_add['單價'], errors='coerce')
                                    
                                    if save_dataframe(pd.concat([df_kept, df_add], ignore_index=True)):
                                        st.success("已刪除"); 
                                        # 清除狀態
                                        st.session_state[f"confirm_del_{conf['key']}"] = False
                                        del st.session_state[f"pending_del_df_{conf['key']}"]
                                        time.sleep(1); st.rerun()
                                        
                        if col_no.button("❌ 否，取消", key=f"no_{conf['key']}"):
                            st.session_state[f"confirm_del_{conf['key']}"] = False
                            if f"pending_del_df_{conf['key']}" in st.session_state:
                                del st.session_state[f"pending_del_df_{conf['key']}"]
                            st.rerun()
                            
                    st.markdown("---")

# --- Tab 3: 收支儀表板 (含分類統計表 & 正確類別名稱顯示) ---
with tab_dash:
    dash_df = df[df['專案'] == global_project].copy()
    if not dash_df.empty:
        dash_df['總價'] = pd.to_numeric(dash_df['總價'], errors='coerce').fillna(0)
        today_str = datetime.now().date(); cur_year = today_str.year
        income_df = dash_df[dash_df['類別'] == '入帳金額']; expense_df = dash_df[dash_df['類別'] != '入帳金額']
        in_total = income_df['總價'].sum(); out_total = expense_df['總價'].sum()
        
        st.markdown(f"### 📊 {cur_year}年 財務概況")
        i1, i2 = st.columns(2); i1.metric("專案總入帳", f"${in_total:,.0f}"); i2.metric("專案總支出", f"${out_total:,.0f}")
        st.divider(); st.metric("💰 專案目前結餘", f"${in_total - out_total:,.0f}")
        
        st.divider()
        st.subheader("支出結構分析")
        col_chart, col_table = st.columns([1.5, 1])
        
        # 1. 圓餅圖
        # 建立映射字典：key -> display name (使用專案獨立的設定)
        cat_map = {c['key']: c['display'] for c in current_cat_config}
        
        # 統計
        chart_df = expense_df.groupby('類別')['總價'].sum().reset_index()
        
        # 將 Key 替換為 Display Name
        chart_df['類別'] = chart_df['類別'].map(cat_map).fillna(chart_df['類別'])
        
        if not chart_df.empty:
            c = alt.Chart(chart_df).mark_arc(innerRadius=50).encode(theta=alt.Theta("總價", stack=True), color=alt.Color("類別", title="類別"), tooltip=["類別", "總價"])
            with col_chart: st.altair_chart(c, use_container_width=True)
            
            # 2. 分類統計表 (呈現詳細數據)
            chart_df['佔比'] = (chart_df['總價'] / out_total * 100).map('{:.1f}%'.format)
            # 格式化金額
            chart_df['金額'] = chart_df['總價'].map('${:,.0f}'.format)
            # 顯示表格 (隱藏原始數值欄位，只顯示格式化後的)
            show_df = chart_df[['類別', '金額', '佔比']]
            with col_table: st.dataframe(show_df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("📄 產出財務報表")
    if not dash_df.empty:
        c_rpt_y, c_rpt_m = st.columns(2)
        rpt_years = sorted(dash_df['Year'].unique().tolist(), reverse=True)
        with c_rpt_y: rpt_sel_year = st.selectbox("報表年份", rpt_years, key="rpt_y")
        rpt_data_y = dash_df[dash_df['Year'] == rpt_sel_year]
        rpt_months = sorted(rpt_data_y['月份'].unique().tolist(), reverse=True)
        with c_rpt_m: rpt_sel_month = st.selectbox("報表月份", ["整年度"] + rpt_months, key="rpt_m")
        if st.button("📥 下載 PDF 報表"):
            rpt_df = rpt_data_y.copy()
            if rpt_sel_month != "整年度": rpt_df = rpt_df[rpt_df['月份'] == rpt_sel_month]
            pdf_data = generate_pdf_report(rpt_df, global_project, rpt_sel_year, rpt_sel_month)
            if pdf_data:
                file_name = f"財務報表_{global_project}_{rpt_sel_year}_{rpt_sel_month}.pdf"
                st.download_button("📥 點此下載 PDF", data=pdf_data, file_name=file_name, mime="application/pdf")

# --- Tab 4: 設定與管理 (從單機版程式碼移植 + Form 優化) ---
with tab_settings:
    st.header("⚙️ 設定與管理")
    
    st.markdown("### 一、專案管理")
    with st.expander("1. 資料備份與還原 (ZIP/CSV)", expanded=False):
        backup_target = st.selectbox("備份對象", ["所有專案 (完整系統)", global_project])
        st.download_button(f"📦 下載備份 ({backup_target})", create_zip_backup(target_project=backup_target), file_name=f"backup_{datetime.now().strftime('%Y%m%d')}.zip", mime="application/zip")
        st.divider()
        uploaded_file = st.file_uploader("📤 系統還原 (請上傳 ZIP 或 CSV)", type=['csv', 'zip'])
        if uploaded_file:
            if st.button("開始還原"):
                try:
                    if uploaded_file.name.endswith('.csv'):
                        if save_dataframe(pd.read_csv(uploaded_file)): st.success("CSV 資料還原成功！")
                    elif uploaded_file.name.endswith('.zip'):
                        with zipfile.ZipFile(uploaded_file, 'r') as z:
                            if 'finance_data.csv' in z.namelist(): save_dataframe(pd.read_csv(z.open('finance_data.csv')))
                            if 'finance_settings.json' in z.namelist(): save_settings(json.load(z.open('finance_settings.json')))
                        st.success("ZIP 還原成功！")
                    time.sleep(1); st.rerun()
                except Exception as e: st.error(f"還原失敗: {e}")

    with st.expander("2. 專案管理 (新增/匯入/改名/刪除)", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("新增與改名")
            with st.form(key="form_add_project"): # FORM
                new_proj = st.text_input("新增專案名稱")
                sub_add_proj = st.form_submit_button("➕ 新增專案")
                if sub_add_proj:
                    if new_proj and new_proj not in settings["projects"]:
                        settings["projects"].append(new_proj)
                        settings["items"][new_proj] = {c["key"]: [] for c in settings["cat_config"]}
                        settings["locations"][new_proj] = {c["key"]: [] for c in settings["cat_config"]}
                        # 複製預設設定給新專案
                        settings["cat_config"][new_proj] = copy.deepcopy(DEFAULT_CAT_CONFIG)
                        save_settings(settings); st.success(f"已新增專案：{new_proj}"); time.sleep(1); st.rerun()
            st.divider()
            with st.form(key="form_ren_project"): # FORM
                rename_proj = st.text_input("修改目前專案名稱", value=global_project)
                sub_ren_proj = st.form_submit_button("✏️ 確認改名")
                if sub_ren_proj:
                    if rename_proj and rename_proj != global_project:
                        settings["projects"] = [rename_proj if p == global_project else p for p in settings["projects"]]
                        settings["items"][rename_proj] = settings["items"].pop(global_project)
                        settings["locations"][rename_proj] = settings["locations"].pop(global_project)
                        settings["cat_config"][rename_proj] = settings["cat_config"].pop(global_project)
                        if global_project in settings.get("item_details", {}):
                            settings["item_details"][rename_proj] = settings["item_details"].pop(global_project)
                        save_settings(settings)
                        if not df.empty: df.loc[df['專案'] == global_project, '專案'] = rename_proj; save_dataframe(df)
                        st.success(f"專案已改名為：{rename_proj}"); time.sleep(1); st.rerun()
        with c2:
            st.subheader("匯入與刪除")
            other_projects = [p for p in settings["projects"] if p != global_project]
            if other_projects:
                source_proj = st.selectbox("📥 從其他專案匯入設定", other_projects)
                if "import_confirm" not in st.session_state: st.session_state.import_confirm = False
                if not st.session_state.import_confirm:
                    if st.button("匯入設定"): st.session_state.import_confirm = True; st.rerun()
                else:
                    st.warning(f"確定要從 {source_proj} 匯入選單項目到 {global_project} 嗎？")
                    iy, in_ = st.columns(2)
                    with iy:
                        if st.button("✔️ 確認匯入"):
                            source_items = settings["items"].get(source_proj, {}); target_items = settings["items"].get(global_project, {})
                            source_locs = settings["locations"].get(source_proj, {}); target_locs = settings["locations"].get(global_project, {})
                            for cat, items in source_items.items():
                                if cat not in target_items: target_items[cat] = []
                                for item in items:
                                    if item not in target_items[cat]: target_items[cat].append(item)
                            for cat, locs in source_locs.items():
                                if cat not in target_locs: target_locs[cat] = []
                                for loc in locs:
                                    if loc not in target_locs[cat]: target_locs[cat].append(loc)
                            save_settings(settings); st.success("匯入完成！"); st.session_state.import_confirm = False; time.sleep(1); st.rerun()
                    with in_:
                        if st.button("❌ 取消匯入"): st.session_state.import_confirm = False; st.rerun()
            st.divider(); st.info(f"正在管理專案：{global_project}")
            if "del_proj_confirm" not in st.session_state: st.session_state.del_proj_confirm = False
            if not st.session_state.del_proj_confirm:
                if st.button("🗑️ 刪除此專案"):
                    if len(settings["projects"]) <= 1: st.error("這是最後一個專案，無法刪除！")
                    else: st.session_state.del_proj_confirm = True; st.rerun()
            else:
                st.warning(f"⚠️ 確定要刪除「{global_project}」嗎？此動作無法復原！")
                col_y, col_n = st.columns(2)
                with col_y:
                    if st.button("✔️ 是，刪除"):
                        settings["projects"].remove(global_project)
                        if global_project in settings["items"]: del settings["items"][global_project]
                        if global_project in settings["locations"]: del settings["locations"][global_project]
                        if global_project in settings["cat_config"]: del settings["cat_config"][global_project]
                        if global_project in settings.get("item_details", {}): del settings["item_details"][global_project]
                        save_settings(settings)
                        if not df.empty: df = df[df['專案'] != global_project]; save_dataframe(df)
                        st.session_state.del_proj_confirm = False; st.success("專案已刪除"); time.sleep(1); st.rerun()
                with col_n:
                    if st.button("❌ 否，取消"): st.session_state.del_proj_confirm = False; st.rerun()
    st.divider(); st.markdown("### 二、大項管理")
    with st.expander("0. 匯入其他專案選單 (覆蓋目前設定)", expanded=True):
        st.info("此功能可將其他專案的選單（細項與地點）複製到目前專案。")
        other_projects = [p for p in settings["projects"] if p != global_project]
        if other_projects:
            source_proj = st.selectbox("📥 選擇來源專案", other_projects)
            if "menu_import_confirm" not in st.session_state: st.session_state.menu_import_confirm = False
            if not st.session_state.menu_import_confirm:
                if st.button("匯入選單"): st.session_state.menu_import_confirm = True; st.rerun()
            else:
                st.warning(f"⚠️ 確定要從【{source_proj}】複製選單到【{global_project}】嗎？這將合併現有項目。")
                iy, in_ = st.columns(2)
                with iy:
                    if st.button("✔️ 確認匯入", key="btn_confirm_menu_imp"):
                        # 複製大項設定
                        settings["cat_config"][global_project] = copy.deepcopy(settings["cat_config"][source_proj])
                        # 複製細項與地點
                        source_items = settings["items"].get(source_proj, {}); target_items = settings["items"].get(global_project, {})
                        source_locs = settings["locations"].get(source_proj, {}); target_locs = settings["locations"].get(global_project, {})
                        for cat, items in source_items.items():
                            if cat not in target_items: target_items[cat] = []
                            for item in items:
                                if item not in target_items[cat]: target_items[cat].append(item)
                        for cat, locs in source_locs.items():
                            if cat not in target_locs: target_locs[cat] = []
                            for loc in locs:
                                if loc not in target_locs[cat]: target_locs[cat].append(loc)
                        save_settings(settings); st.success("選單匯入成功！"); st.session_state.menu_import_confirm = False; time.sleep(1); st.rerun()
                with in_:
                    if st.button("❌ 取消", key="btn_cancel_menu_imp"): st.session_state.menu_import_confirm = False; st.rerun()
        else: st.warning("目前只有一個專案，無法執行匯入。")
    with st.expander("1. 增加紀錄項目", expanded=False):
        st.subheader("➕ 新增管理項目")
        with st.form(key="form_add_cat"): # FORM
            nc1, nc2, nc3 = st.columns([2, 1, 1])
            with nc1: new_cat_name = st.text_input("區塊名稱 (例：08. 人事費)")
            with nc2: new_cat_type = st.selectbox("類型", ["expense", "income"], format_func=lambda x: "支出" if x=="expense" else "收入")
            with nc3: 
                st.write("")
                sub_add_cat = st.form_submit_button("新增")
                if sub_add_cat:
                    if new_cat_name:
                        new_key = new_cat_name
                        if any(c['key'] == new_key for c in current_cat_config): st.error("名稱重複！")
                        else:
                            current_cat_config.append({"key": new_key, "display": new_cat_name, "type": new_cat_type})
                            for proj in settings["items"]:
                                if new_key not in settings["items"][proj]: settings["items"][proj][new_key] = []
                                if new_key not in settings["locations"][proj]: settings["locations"][proj][new_key] = []
                            save_settings(settings); st.success("已新增"); time.sleep(0.5); st.rerun()
    with st.expander("2. 記錄項目管理 (修改標題/新增/刪除)", expanded=False):
        st.info("此處修改會影響所有專案的選單顯示。")
        for idx, cat in enumerate(current_cat_config):
            c_label, c_input, c_btn, c_del = st.columns([2, 3, 1, 1])
            with c_label: st.text(f"原標題: {cat['display']}")
            with c_input: new_display = st.text_input(f"新名稱 {idx}", value=cat["display"], label_visibility="collapsed", key=f"cat_ren_{idx}")
            with c_btn:
                if new_display != cat["display"]:
                    if st.button("更新", key=f"btn_upd_cat_{idx}"):
                        current_cat_config[idx]["display"] = new_display; save_settings(settings); st.success("標題已更新"); time.sleep(0.5); st.rerun()
            with c_del:
                del_cat_key = f"del_cat_{idx}_confirm"
                if del_cat_key not in st.session_state: st.session_state[del_cat_key] = False
                if not st.session_state[del_cat_key]:
                    if st.button("刪除", key=f"btn_del_cat_{idx}"): st.session_state[del_cat_key] = True; st.rerun()
                else:
                    if st.button("✔️", key=f"yes_cat_{idx}"):
                        current_cat_config.pop(idx); save_settings(settings); st.session_state[del_cat_key] = False; st.rerun()
                    if st.button("❌", key=f"no_cat_{idx}"): st.session_state[del_cat_key] = False; st.rerun()
    with st.expander("3. 細項選單管理 (修改標題/新增/刪除)", expanded=True):
        target_cat = st.selectbox("選擇要管理的大項", [c["display"] for c in current_cat_config])
        cat_key = next(c["key"] for c in current_cat_config if c["display"] == target_cat)
        cat_type = next(c["type"] for c in current_cat_config if c["display"] == target_cat)
        if global_project not in settings["items"]: settings["items"][global_project] = {c["key"]: [] for c in current_cat_config}
        if cat_key not in settings["items"][global_project]: settings["items"][global_project][cat_key] = []
        if global_project not in settings["locations"]: settings["locations"][global_project] = {c["key"]: [] for c in current_cat_config}
        if cat_key not in settings["locations"][global_project]: settings["locations"][global_project][cat_key] = []
        
        if global_project not in settings.get("item_details", {}): settings.setdefault("item_details", {})[global_project] = {}

        if cat_type == "income":
            manage_mode_display = "💰 入帳項目 (Items)"; list_type = "item"
            current_list = settings["items"][global_project][cat_key]
            placeholder_txt = "輸入入帳來源 (如: 零用金撥款)"; st.markdown(f"**管理【{target_cat}】的入帳來源**")
        else:
            mode_sel = st.radio("選擇要管理的清單", ["📦 購買內容 (Items)", "📍 購買地點 (Locations)"], horizontal=True)
            if "內容" in mode_sel:
                manage_mode_display = mode_sel; list_type = "item"
                current_list = settings["items"][global_project][cat_key]; placeholder_txt = "輸入細項名稱 (如: 水泥、砂石)"
            else:
                manage_mode_display = mode_sel; list_type = "location"
                current_list = settings["locations"][global_project][cat_key]; placeholder_txt = "輸入地點名稱 (如: 五金行、加油站)"
            st.markdown(f"在【{target_cat}】中新增 **{manage_mode_display.split()[1]}**")
        
        with st.form(key=f"form_add_item_{list_type}"): # FORM
            c_add1, c_add2 = st.columns([4, 1])
            with c_add1: new_item = st.text_input(placeholder_txt, key=f"new_{list_type}_input", label_visibility="collapsed")
            with c_add2:
                sub_add_item = st.form_submit_button("➕ 加入")
                if sub_add_item:
                    if new_item and new_item not in current_list:
                        if list_type == "item": settings["items"][global_project][cat_key].append(new_item)
                        else: settings["locations"][global_project][cat_key].append(new_item)
                        # Init price
                        if list_type == "item":
                            settings["item_details"][global_project][new_item] = {"price": 0, "unit": "式"}
                        save_settings(settings); st.success("已加入"); st.rerun()
        
        if current_list:
            st.markdown(f"#### 管理現有 {manage_mode_display.split()[1]}")
            
            if list_type == "item":
                # Item 模式 (含單價/單位)
                h1, h2, h3, h4, h5 = st.columns([2, 1.5, 1, 0.5, 0.5])
                h1.markdown("**項目名稱**"); h2.markdown("**預設單價**"); h3.markdown("**單位**")
                
                for i, it in enumerate(current_list):
                    ic1, ic2, ic3, ic4, ic5 = st.columns([2, 1.5, 1, 0.5, 0.5])
                    curr_detail = settings["item_details"][global_project].get(it, {"price": 0, "unit": "式"})
                    
                    with ic1: rn = st.text_input("N", it, key=f"item_rn_{i}", label_visibility="collapsed")
                    with ic2: rp = st.number_input("P", value=int(curr_detail.get("price", 0)), step=100, key=f"item_rp_{i}", label_visibility="collapsed")
                    with ic3: ru = st.text_input("U", value=curr_detail.get("unit", "式"), key=f"item_ru_{i}", label_visibility="collapsed")
                    
                    with ic4:
                        if st.button("💾", key=f"item_sv_{i}"):
                            # 1. Update Name
                            if rn != it:
                                settings["items"][global_project][cat_key][i] = rn
                                if not df.empty:
                                    mask = (df['專案'] == global_project) & (df['類別'] == cat_key) & (df['項目內容'] == it)
                                    df.loc[mask, '項目內容'] = rn; save_dataframe(df)
                                if it in settings["item_details"][global_project]:
                                    del settings["item_details"][global_project][it]
                            # 2. Update Details
                            settings["item_details"][global_project][rn] = {"price": rp, "unit": ru}
                            save_settings(settings); st.toast("已更新"); time.sleep(0.5); st.rerun()
                    with ic5:
                        del_sub_key = f"del_item_confirm_{i}_{list_type}"
                        if del_sub_key not in st.session_state: st.session_state[del_sub_key] = False
                        if not st.session_state[del_sub_key]:
                            if st.button("🗑️", key=f"item_rm_{i}"): st.session_state[del_sub_key] = True; st.rerun()
                        else:
                            if st.button("✔️", key=f"item_yes_{i}"):
                                settings["items"][global_project][cat_key].remove(it)
                                if it in settings["item_details"][global_project]: del settings["item_details"][global_project][it]
                                save_settings(settings); st.session_state[del_sub_key] = False; st.rerun()
                            if st.button("❌", key=f"item_no_{i}"): st.session_state[del_sub_key] = False; st.rerun()
            else:
                # Location 模式
                h1, h2, h3, h4 = st.columns([2, 3, 1, 1])
                h1.markdown("**原名稱**"); h2.markdown("**改名**"); h3.markdown("**存**"); h4.markdown("**刪**")
                for i, item in enumerate(current_list):
                    ic1, ic2, ic3, ic4 = st.columns([2, 3, 1, 1])
                    with ic1: st.text(item)
                    with ic2: ren_item = st.text_input("改名", value=item, key=f"ren_{list_type}_{i}", label_visibility="collapsed")
                    with ic3:
                        if ren_item != item:
                            if st.button("💾", key=f"save_{list_type}_{i}"):
                                settings["locations"][global_project][cat_key][i] = ren_item
                                if not df.empty:
                                    mask = (df['專案'] == global_project) & (df['類別'] == cat_key) & (df['購買地點'] == item)
                                    df.loc[mask, '購買地點'] = ren_item; save_dataframe(df)
                                save_settings(settings); st.toast("名稱已更新"); time.sleep(0.5); st.rerun()
                        else: st.button("💾", key=f"save_{list_type}_{i}", disabled=True)
                    with ic4:
                        del_sub_key = f"del_{list_type}_{i}_confirm"
                        if del_sub_key not in st.session_state: st.session_state[del_sub_key] = False
                        if not st.session_state[del_sub_key]:
                            if st.button("🗑️", key=f"del_{list_type}_{i}"): st.session_state[del_sub_key] = True; st.rerun()
                        else:
                            if st.button("✔️", key=f"yes_{list_type}_{i}"):
                                settings["locations"][global_project][cat_key].remove(item)
                                save_settings(settings); st.session_state[del_sub_key] = False; st.rerun()
                            if st.button("❌", key=f"no_{list_type}_{i}"): st.session_state[del_sub_key] = False; st.rerun()

        else: st.info(f"此類別目前沒有設定常用{manage_mode_display.split()[1]}。")




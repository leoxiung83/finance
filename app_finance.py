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

# 設定頁面
st.set_page_config(page_title="勁翔營造 工地計帳系統", layout="wide", page_icon="🏗️")

# --- 1. 安全匯入機制 (防止崩潰) ---
try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    HAS_GOOGLE_LIB = True
except ImportError:
    HAS_GOOGLE_LIB = False

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.units import cm
    HAS_PDF_LIB = True
except ImportError:
    HAS_PDF_LIB = False

# --- 檔案與字型設定 ---
DATA_FILE = 'finance_data.csv'
SETTINGS_FILE = 'finance_settings.json'
FONT_FILE = 'kaiu.ttf' 
FONT_NAME = 'Kaiu'

# --- 判斷執行模式 ---
def check_mode():
    # 優先檢查是否具備雲端條件
    if HAS_GOOGLE_LIB:
        try:
            # 檢查 secrets 是否存在 (Streamlit Cloud 或本地 .streamlit/secrets.toml)
            if "gcp_service_account" in st.secrets:
                return "cloud"
        except:
            pass
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
# 1. 資料存取層
# ==========================================

def get_gsheet_client():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    return gspread.authorize(creds)

@st.cache_data(ttl=10)
def load_data():
    cols = ['日期', '專案', '類別', '項目內容', '單位', '數量', '單價', '總價', '購買地點', '經手人', '憑證類型', '發票號碼', '備註', '月份', 'Year']
    
    if MODE == "cloud":
        try:
            client = get_gsheet_client()
            sheet = client.open("FinanceData").sheet1
            data = sheet.get_all_records()
            df = pd.DataFrame(data) if data else pd.DataFrame(columns=cols)
            for c in cols:
                if c not in df.columns: df[c] = ""
        except Exception as e:
            st.warning(f"⚠️ 雲端讀取異常 ({e})，切換至暫存模式。")
            return pd.DataFrame(columns=cols)
    else:
        # 本地模式
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
            sheet = client.open("FinanceData").sheet1
            df_save['日期'] = df_save['日期'].astype(str)
            sheet.clear()
            sheet.update([df_save.columns.values.tolist()] + df_save.values.tolist())
            load_data.clear()
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
        "cat_config": DEFAULT_CAT_CONFIG
    }
    
    if MODE == "cloud":
        try:
            client = get_gsheet_client()
            try:
                ws = client.open("FinanceData").worksheet("Settings")
                json_str = ws.acell('A1').value
                if json_str: return json.loads(json_str)
            except:
                pass
    else:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    return default

def save_settings(data):
    if MODE == "cloud":
        try:
            client = get_gsheet_client()
            try:
                ws = client.open("FinanceData").worksheet("Settings")
                ws.update('A1', [[json.dumps(data, ensure_ascii=False)]])
            except:
                st.warning("雲端無 'Settings' 分頁。")
    else:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)

def append_record(record_dict):
    if MODE == "cloud":
        try:
            client = get_gsheet_client()
            sheet = client.open("FinanceData").sheet1
            row = [
                str(record_dict['日期']), record_dict['專案'], record_dict['類別'], record_dict['項目內容'],
                record_dict['單位'], record_dict['數量'], record_dict['單價'], record_dict['總價'],
                record_dict['購買地點'], record_dict['經手人'], record_dict['憑證類型'],
                str(record_dict['發票號碼']), record_dict['備註']
            ]
            sheet.append_row(row)
            load_data.clear()
        except Exception as e:
            st.error(f"雲端寫入錯誤: {e}")
    else:
        current_df = load_data()
        new_df = pd.DataFrame([record_dict])
        updated_df = pd.concat([current_df, new_df], ignore_index=True)
        save_dataframe(updated_df)

def create_zip_backup(df, settings, target_project):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        if target_project and target_project != "所有專案 (完整系統)":
            df_out = df[df['專案'] == target_project] if not df.empty else df
            s_out = {
                "projects": [target_project],
                "cat_config": settings.get("cat_config", DEFAULT_CAT_CONFIG),
                "items": {target_project: settings.get("items", {}).get(target_project, {})},
                "locations": {target_project: settings.get("locations", {}).get(target_project, {})}
            }
        else:
            df_out = df
            s_out = settings
            
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

# --- PDF 生成 (安全版) ---
def generate_pdf_report(df, project_name, year, month):
    if not HAS_PDF_LIB:
        st.error("系統缺少 'reportlab' 套件，無法產生 PDF。請確認 requirements.txt。")
        return None
        
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=1.0*cm, leftMargin=1.0*cm, topMargin=1.5*cm, bottomMargin=1.5*cm)
    
    font_path = FONT_FILE 
    if not os.path.exists(font_path):
        font_main = 'Helvetica'; font_bold = 'Helvetica-Bold'
        # 若雲端缺少字型，顯示提示但不中斷
        st.toast(f"⚠️ 找不到 {FONT_FILE}，報表將使用預設字型 (中文可能無法顯示)。")
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
    style_title = ParagraphStyle(name='Title_TC', parent=styles['Heading1'], fontName=font_bold, fontSize=24, leading=32, alignment=1, textColor=accent_color, spaceAfter=6)
    style_subtitle = ParagraphStyle(name='SubTitle_TC', parent=styles['Heading2'], fontName=font_main, fontSize=14, leading=18, alignment=1, textColor=colors.black, spaceAfter=20)
    style_h2 = ParagraphStyle(name='H2_TC', parent=styles['Heading2'], fontName=font_bold, fontSize=16, leading=20, spaceBefore=15, spaceAfter=10, textColor=accent_color, keepWithNext=True)
    style_h3 = ParagraphStyle(name='H3_TC', parent=styles['Heading3'], fontName=font_bold, fontSize=12, leading=16, spaceBefore=12, spaceAfter=6, textColor=colors.black, keepWithNext=True)
    style_table_cell = ParagraphStyle(name='TableCell_TC', parent=styles['Normal'], fontName=font_main, fontSize=10, leading=12)

    elements = []
    if month == "整年度": time_display = f"{year}年年報"
    else: m_only = month.split('-')[1]; time_display = f"{year}年{m_only}月份"

    elements.append(Paragraph("勁翔營造工地支出報表", style_title))
    elements.append(Paragraph(time_display, style_subtitle))
    
    info_data = [[f"專案名稱：{project_name}", f"列印時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}"]]
    t_info = Table(info_data, colWidths=[300, 240])
    t_info.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_main), ('FONTSIZE', (0,0), (-1,-1), 10), ('ALIGN', (0,0), (0,0), 'LEFT'), ('ALIGN', (1,0), (1,0), 'RIGHT')]))
    elements.append(t_info)
    elements.append(HRFlowable(width="100%", thickness=2, color=accent_color, spaceBefore=5, spaceAfter=15))

    elements.append(Paragraph("一、財務總覽", style_h2))
    rpt_inc = df[df['類別'] == '入帳金額']['總價'].sum()
    rpt_exp = df[df['類別'] != '入帳金額']['總價'].sum()
    rpt_bal = rpt_inc - rpt_exp
    data_summary = [['項目', '總入帳', '總支出', '目前結餘'], ['金額', f"${rpt_inc:,.0f}", f"${rpt_exp:,.0f}", f"${rpt_bal:,.0f}"]]
    t_sum = Table(data_summary, colWidths=[100, 140, 140, 140], hAlign='LEFT')
    t_sum.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_main), ('FONTSIZE', (0,0), (-1,-1), 11), ('LEADING', (0,0), (-1,-1), 16), ('BACKGROUND', (0,0), (-1,0), accent_color), ('TEXTCOLOR', (0,0), (-1,0), header_text_color), ('FONTNAME', (0,0), (-1,0), font_bold), ('ALIGN', (0,0), (-1,0), 'CENTER'), ('ALIGN', (1,1), (-1,1), 'RIGHT'), ('BACKGROUND', (0,1), (-1,1), summary_bg), ('GRID', (0,0), (-1,-1), 1, colors.grey), ('TEXTCOLOR', (3,1), (3,1), colors.red if rpt_bal < 0 else accent_color), ('FONTNAME', (3,1), (3,1), font_bold)]))
    elements.append(t_sum)
    elements.append(Spacer(1, 20))
    
    elements.append(Paragraph("二、支出結構分析", style_h2))
    exp_summary = df[df['類別'] != '入帳金額'].groupby('類別')['總價'].sum().reset_index().sort_values('總價', ascending=False)
    if not exp_summary.empty:
        exp_data = [['支出大項', '金額', '佔比']]
        for i, row in exp_summary.iterrows():
            pct = (row['總價'] / rpt_exp * 100) if rpt_exp > 0 else 0
            exp_data.append([row['類別'], f"${row['總價']:,.0f}", f"{pct:.1f}%"])
        t_exp = Table(exp_data, colWidths=[200, 120, 80], hAlign='LEFT')
        zebra_styles = [('BACKGROUND', (0, i), (-1, i), zebra_bg_odd if i % 2 != 0 else zebra_bg_even) for i in range(1, len(exp_data))]
        t_exp.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_main), ('FONTSIZE', (0,0), (-1,-1), 10), ('LEADING', (0,0), (-1,-1), 14), ('BACKGROUND', (0,0), (-1,0), header_bg_color), ('TEXTCOLOR', (0,0), (-1,0), header_text_color), ('FONTNAME', (0,0), (-1,0), font_bold), ('ALIGN', (1,0), (-1,-1), 'RIGHT'), ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey), ('BOX', (0,0), (-1,-1), 1, accent_color)] + zebra_styles))
        elements.append(t_exp)
    elements.append(Spacer(1, 20))
    
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
        detail_zebra = [('BACKGROUND', (0, i), (-1, i), zebra_bg_odd if i % 2 != 0 else zebra_bg_even) for i in range(1, len(table_data))]
        t_detail.setStyle(TableStyle([('FONTNAME', (0,0), (-1,-1), font_main), ('FONTSIZE', (0,0), (-1,-1), 10), ('LEADING', (0,0), (-1,-1), 12), ('BACKGROUND', (0,0), (-1,0), header_bg_color), ('TEXTCOLOR', (0,0), (-1,0), header_text_color), ('FONTNAME', (0,0), (-1,0), font_bold), ('ALIGN', (0,0), (-1,-1), 'LEFT'), ('ALIGN', (4,0), (6,-1), 'RIGHT'), ('VALIGN', (0,0), (-1,-1), 'TOP'), ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey), ('BOX', (0,0), (-1,-1), 1, accent_color)] + detail_zebra))
        elements.append(t_detail)
        elements.append(Spacer(1, 20))
    doc.build(elements)
    buffer.seek(0)
    return buffer

# ==========================================
# 3. UI 介面
# ==========================================

settings = load_settings()
df = load_data()

st.title("🏗️ 勁翔營造 工地計帳系統")
if MODE == "local":
    if not HAS_GOOGLE_LIB:
        st.warning("⚠️ 單機模式 (缺少 gspread 套件，無法連線 Google Sheets)")
    elif "gcp_service_account" not in st.secrets:
        st.warning("⚠️ 單機模式 (未偵測到 Secrets 金鑰)")
    else:
        st.info("💻 單機模式 (連線失敗，使用本地 CSV)")
else:
    st.toast("☁️ 雲端連線模式：資料同步儲存於 Google Sheets")

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

tab_entry, tab_data, tab_dash, tab_settings = st.tabs(["📝 支出填寫", "📋 明細管理", "📊 收支儀表板", "⚙️ 設定與管理"])

# --- Tab 1: 支出填寫 ---
with tab_entry:
    st.info(f"📍 當前專案：{global_project} | 日期：{global_date} {day_str}")
    def handle_save_tab1(conf_key, conf_type, display_name):
        k_sel = f"sel_{conf_key}"; k_man = f"man_{conf_key}"; k_sel_loc = f"sel_loc_{conf_key}"
        k_man_loc = f"man_loc_{conf_key}"; k_loc = f"loc_{conf_key}"; k_buyer = f"buyer_{conf_key}"
        k_type = f"type_{conf_key}"; k_inv = f"inv_{conf_key}"; k_qty = f"qty_{conf_key}"
        k_unit = f"unit_{conf_key}"; k_price = f"price_{conf_key}"; k_note = f"note_{conf_key}"

        sel_val = st.session_state.get(k_sel, ""); man_val = st.session_state.get(k_man, "")
        final_item = man_val if sel_val == "✏️ 手動輸入..." else sel_val
        
        if conf_type == "income":
            location, r_type, inv_no, qty, unit = "", "無", "", 1, "次"
            price = st.session_state.get(k_price, 0); handler = st.session_state.get(k_buyer, "")
        else:
            sel_loc_val = st.session_state.get(k_sel_loc, ""); man_loc_val = st.session_state.get(k_man_loc, "")
            location = man_loc_val if sel_loc_val == "✏️ 手動輸入..." else sel_loc_val
            if not location: location = st.session_state.get(k_loc, "")
            handler = st.session_state.get(k_buyer, ""); r_type = st.session_state.get(k_type, "收據")
            inv_no = st.session_state.get(k_inv, "") if r_type == "發票" else ""
            qty = st.session_state.get(k_qty, 1.0); unit = st.session_state.get(k_unit, "式")
            price = st.session_state.get(k_price, 0)

        note = st.session_state.get(k_note, "")
        if not final_item: st.toast(f"❌ 請輸入 {display_name} 的項目/來源！", icon="⚠️"); return

        record = {
            '日期': global_date, '專案': global_project, '類別': conf_key, '項目內容': final_item,
            '單位': unit, '數量': qty, '單價': price, '總價': qty*price, '購買地點': location,
            '經手人': handler, '憑證類型': r_type, '發票號碼': inv_no, '備註': note
        }
        append_record(record)
        st.toast(f"✅ {display_name} 儲存成功！")
        st.session_state[k_man] = ""; st.session_state[k_price] = 0; st.session_state[k_note] = ""; st.session_state[k_buyer] = ""
        if conf_type != "income": st.session_state[k_man_loc] = ""; st.session_state[k_inv] = ""; st.session_state[k_qty] = 1.0

    for conf in settings["cat_config"]:
        icon = "💰" if conf["type"] == "income" else "💸"
        k_sel = f"sel_{conf['key']}"; k_man = f"man_{conf['key']}"; k_price = f"price_{conf['key']}"
        k_buyer = f"buyer_{conf['key']}"; k_note = f"note_{conf['key']}"; k_sel_loc = f"sel_loc_{conf['key']}"
        k_man_loc = f"man_loc_{conf_key}"; k_type = f"type_{conf['key']}"; k_inv = f"inv_{conf['key']}"
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

# --- Tab 2: 明細管理 ---
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
            for conf in settings["cat_config"]:
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
                    
                    edited_cat = st.data_editor(cat_df.sort_values('日期', ascending=False), column_config=col_config, use_container_width=True, num_rows="dynamic", key=f"editor_{conf['key']}_{sel_year}_{sel_month}", hide_index=True)
                    
                    c_btn1, c_btn2, _ = st.columns([1, 1, 4])
                    if c_btn1.button("💾 更新修改", key=f"btn_upd_{conf['key']}"):
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

                    if c_btn2.button("🗑️ 刪除選取", key=f"btn_del_{conf['key']}"):
                        if not edited_cat['刪除'].any(): st.warning("請勾選刪除項目")
                        elif search_kw: st.error("搜尋模式下無法刪除")
                        else:
                            with st.spinner("正在刪除..."):
                                rows_keep = edited_cat[edited_cat['刪除'] == False].copy()
                                current_full_df = df
                                mask = (current_full_df['專案'] == global_project) & (current_full_df['類別'] == conf['key']) & (current_full_df['Year'] == sel_year)
                                if sel_month != "整年": mask = mask & (current_full_df['月份'] == sel_month)
                                df_kept = current_full_df[~mask]
                                df_add = rows_keep.drop(columns=['刪除', '星期/節日'], errors='ignore')
                                df_add['類別'] = conf['key']; df_add['專案'] = global_project
                                df_add['總價'] = pd.to_numeric(df_add['數量'], errors='coerce') * pd.to_numeric(df_add['單價'], errors='coerce')
                                if save_dataframe(pd.concat([df_kept, df_add], ignore_index=True)): st.success("已刪除"); time.sleep(1); st.rerun()
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
        i1, i2 = st.columns(2); i1.metric("專案總入帳", f"${in_total:,.0f}"); i2.metric("專案總支出", f"${out_total:,.0f}")
        st.divider(); st.metric("💰 專案目前結餘", f"${in_total - out_total:,.0f}"); st.divider()
        chart_df = expense_df.groupby('類別')['總價'].sum().reset_index()
        if not chart_df.empty:
            c = alt.Chart(chart_df).mark_arc(innerRadius=50).encode(theta=alt.Theta("總價", stack=True), color="類別", tooltip=["類別", "總價"])
            st.altair_chart(c, use_container_width=True)
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

# --- Tab 4: 設定與管理 (全功能) ---
with tab_settings:
    st.header("⚙️ 設定與管理")
    st.markdown("### 一、專案管理")
    with st.expander("1. 資料備份與還原", expanded=False):
        backup_target = st.selectbox("備份對象", ["所有專案 (完整系統)", global_project])
        st.download_button(f"📦 下載備份 ({backup_target})", create_zip_backup(df, settings, backup_target), file_name=f"backup_{datetime.now().strftime('%Y%m%d')}.zip", mime="application/zip")
        st.divider()
        uploaded_file = st.file_uploader("📤 系統還原 (ZIP/CSV)", type=['csv', 'zip'])
        if uploaded_file and st.button("開始還原"):
            try:
                if uploaded_file.name.endswith('.csv'):
                    if save_dataframe(pd.read_csv(uploaded_file)): st.success("CSV 還原成功！")
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
            new_proj = st.text_input("新增專案名稱")
            if st.button("➕ 新增"):
                if new_proj and new_proj not in settings["projects"]:
                    settings["projects"].append(new_proj)
                    settings["items"][new_proj] = {c["key"]: [] for c in settings["cat_config"]}
                    settings["locations"][new_proj] = {c["key"]: [] for c in settings["cat_config"]}
                    save_settings(settings); st.success("已新增"); st.rerun()
            st.divider()
            rename_proj = st.text_input("改名目前專案", value=global_project)
            if st.button("✏️ 改名"):
                if rename_proj and rename_proj != global_project:
                    settings["projects"] = [rename_proj if p == global_project else p for p in settings["projects"]]
                    settings["items"][rename_proj] = settings["items"].pop(global_project)
                    settings["locations"][rename_proj] = settings["locations"].pop(global_project)
                    save_settings(settings)
                    if not df.empty:
                        df.loc[df['專案'] == global_project, '專案'] = rename_proj
                        save_dataframe(df)
                    st.success("專案已改名"); st.rerun()
        with c2:
            op = [p for p in settings["projects"] if p != global_project]
            if op:
                source_proj = st.selectbox("📥 匯入來源", op)
                if st.button("匯入設定"):
                    s_i = settings["items"].get(source_proj, {}); t_i = settings["items"].get(global_project, {})
                    s_l = settings["locations"].get(source_proj, {}); t_l = settings["locations"].get(global_project, {})
                    for c, items in s_i.items():
                        for it in items: 
                            if it not in t_i[c]: t_i[c].append(it)
                    for c, locs in s_l.items():
                        for l in locs:
                            if l not in t_l[c]: t_l[c].append(l)
                    save_settings(settings); st.success("匯入完成"); st.rerun()
            st.divider()
            if st.button("🗑️ 刪除此專案"):
                if len(settings["projects"]) <= 1: st.error("無法刪除最後一個專案")
                else:
                    settings["projects"].remove(global_project)
                    del settings["items"][global_project]; del settings["locations"][global_project]
                    save_settings(settings)
                    if not df.empty: save_dataframe(df[df['專案'] != global_project])
                    st.success("專案已刪除"); st.rerun()

    st.markdown("### 二、分類管理")
    with st.expander("1. 大項管理 (類別)", expanded=False):
        nc1, nc2, nc3 = st.columns([2, 1, 1])
        with nc1: new_cat = st.text_input("類別名稱")
        with nc2: new_type = st.selectbox("類型", ["expense", "income"])
        with nc3: 
            st.write(""); 
            if st.button("新增類別"):
                if new_cat and not any(c['key'] == new_cat for c in settings["cat_config"]):
                    settings["cat_config"].append({"key": new_cat, "display": new_cat, "type": new_type})
                    for p in settings["items"]:
                        settings["items"][p][new_cat] = []; settings["locations"][p][new_cat] = []
                    save_settings(settings); st.rerun()
        for i, c in enumerate(settings["cat_config"]):
            c1, c2, c3 = st.columns([3, 1, 1])
            with c1: new_disp = st.text_input(f"名稱 {i}", c['display'], key=f"rn_{i}", label_visibility="collapsed")
            with c2: 
                if st.button("更", key=f"up_{i}"): 
                    settings["cat_config"][i]["display"] = new_disp; save_settings(settings); st.rerun()
            with c3:
                if st.button("刪", key=f"dl_{i}"):
                    settings["cat_config"].pop(i); save_settings(settings); st.rerun()

    with st.expander("2. 細項管理 (項目/地點)", expanded=True):
        t_cat = st.selectbox("選擇大項", [c["display"] for c in settings["cat_config"]])
        c_key = next(c["key"] for c in settings["cat_config"] if c["display"] == t_cat)
        c_type = next(c["type"] for c in settings["cat_config"] if c["display"] == t_cat)
        
        # 確保結構
        if global_project not in settings["items"]: settings["items"][global_project] = {c["key"]: [] for c in settings["cat_config"]}
        if c_key not in settings["items"][global_project]: settings["items"][global_project][c_key] = []
        if global_project not in settings["locations"]: settings["locations"][global_project] = {c["key"]: [] for c in settings["cat_config"]}

        list_type = "item"
        if c_type != "income":
            mode = st.radio("管理清單", ["內容 (Items)", "地點 (Locations)"], horizontal=True)
            list_type = "item" if "內容" in mode else "location"
        
        curr_list = settings["items"][global_project][c_key] if list_type == "item" else settings["locations"][global_project][c_key]
        
        c_add1, c_add2 = st.columns([4, 1])
        with c_add1: new_it = st.text_input("新項目名稱")
        with c_add2:
            if st.button("➕"):
                if new_it and new_it not in curr_list:
                    if list_type == "item": settings["items"][global_project][c_key].append(new_it)
                    else: settings["locations"][global_project][c_key].append(new_it)
                    save_settings(settings); st.rerun()
        
        for i, it in enumerate(curr_list):
            ic1, ic2, ic3, ic4 = st.columns([2, 3, 1, 1])
            with ic1: st.text(it)
            with ic2: rn = st.text_input("改名", it, key=f"rni_{i}", label_visibility="collapsed")
            with ic3:
                if st.button("💾", key=f"sv_{i}"):
                    if list_type == "item":
                        settings["items"][global_project][c_key][i] = rn
                        if not df.empty:
                            mask = (df['專案'] == global_project) & (df['類別'] == c_key) & (df['項目內容'] == it)
                            df.loc[mask, '項目內容'] = rn; save_dataframe(df)
                    else:
                        settings["locations"][global_project][c_key][i] = rn
                        if not df.empty:
                            mask = (df['專案'] == global_project) & (df['類別'] == c_key) & (df['購買地點'] == it)
                            df.loc[mask, '購買地點'] = rn; save_dataframe(df)
                    save_settings(settings); st.success("已更新"); time.sleep(0.5); st.rerun()
            with ic4:
                if st.button("🗑️", key=f"rm_{i}"):
                    if list_type == "item": settings["items"][global_project][c_key].remove(it)
                    else: settings["locations"][global_project][c_key].remove(it)
                    save_settings(settings); st.rerun()
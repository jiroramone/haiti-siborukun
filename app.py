import streamlit as st
import pandas as pd
import numpy as np
import re
import requests
from bs4 import BeautifulSoup

# --- 1. 基本設定 ---
st.set_page_config(page_title="配置馬券術 分析システム", layout="wide")

def to_half_width(text):
    if pd.isna(text): return text
    text = str(text)
    table = str.maketrans('０１２３４５６７８９．', '0123456789.')
    return re.sub(r'[^\d\.]', '', text.translate(table))

def normalize_name(x):
    if pd.isna(x): return ''
    return re.sub(r'[★☆▲△◇]', '', str(x).strip().replace('　', '').replace(' ', ''))

# --- 2. データ読み込み ---
@st.cache_data
def load_data(file):
    try:
        if file.name.endswith('.xlsx'):
            df = pd.read_excel(file, engine='openpyxl')
        else:
            try: df = pd.read_csv(file, encoding='utf-8')
            except: df = pd.read_csv(file, encoding='cp932')
        
        # ヘッダー特定
        if not any(col in str(df.columns) for col in ['馬', '番', 'R', '騎']):
            for i in range(min(len(df), 10)):
                if any(x in str(df.iloc[i].values) for x in ['馬', '番', 'R']):
                    df.columns = df.iloc[i]; df = df.iloc[i+1:].reset_index(drop=True); break

        df.columns = df.columns.astype(str).str.strip()
        name_map = {
            '場所': '場名', '開催': '場名', '競馬場': '場名',
            '調教師': '厩舎', '調教師名': '厩舎', '厩舎名': '厩舎',
            '騎手名': '騎手', 'レース': 'R', '番': '正番', '馬番': '正番',
            '単オッズ': '単ｵｯｽﾞ', '単勝オッズ': '単ｵｯｽﾞ', 'オッズ': '単ｵｯｽﾞ'
        }
        df = df.rename(columns=name_map)
        
        # 数値化
        df['R'] = pd.to_numeric(df['R'].apply(to_half_width), errors='coerce')
        df['正番'] = pd.to_numeric(df['正番'].apply(to_half_width), errors='coerce')
        df = df.dropna(subset=['R', '正番'])
        df['R'] = df['R'].astype(int); df['正番'] = df['正番'].astype(int)

        for col in ['騎手', '厩舎', '馬主', '馬名', '場名']:
            if col in df.columns: df[col] = df[col].apply(normalize_name)
        
        if '単ｵｯｽﾞ' in df.columns:
            df['単ｵｯｽﾞ'] = pd.to_numeric(df['単ｵｯｽﾞ'].apply(to_half_width), errors='coerce')
        else:
            df['単ｵｯｽﾞ'] = np.nan
        
        return df.copy(), "success"
    except Exception as e: return pd.DataFrame(), str(e)

# --- 3. 配置計算エンジン (青塗・隣・ペア 完全版) ---
def analyze_haichi(df):
    df = df.copy()
    
    # 基礎数値計算
    max_umaban = df.groupby(['場名', 'R'])['正番'].transform('max')
    df['頭数'] = max_umaban.fillna(16).astype(int)
    if '頭数' in df.columns: # Excelに頭数があれば優先
         df['頭数'] = pd.to_numeric(df['頭数'], errors='coerce').fillna(df['頭数']).astype(int)
         
    df['逆番'] = (df['頭数'] + 1) - df['正番']
    df['正循環'] = df['頭数'] + df['正番']
    df['逆循環'] = df['頭数'] + df['逆番']

    # 結果格納用リスト（ここに該当データを積んでいく）
    # 初期状態として全データを入れる
    results = df.to_dict('records')
    for r in results:
        r['タイプ'] = []
        r['パターン'] = []
        r['条件'] = []
        r['スコア'] = 0.0

    # 検索用辞書作成
    res_map = {}
    for r in results:
        res_map[(r['場名'], r['R'], r['正番'])] = r

    # --- A. 青塗分析 (Logic A) ---
    blue_horses = [] # (場名, R, 正番, 属性名, オッズ)
    
    for col in ['騎手', '厩舎', '馬主']:
        if col not in df.columns: continue
        group_keys = ['場名', col] if col == '騎手' else [col]
        
        # グループごとに共通値を探索
        for name, group in df.groupby(group_keys):
            if len(group) < 2 or not name: continue
            
            cols_val = ['正番', '逆番', '正循環', '逆循環']
            common = None
            for _, row in group.iterrows():
                cur_v = {int(row[c]) for c in cols_val if pd.notna(row[c])}
                common = cur_v if common is None else common.intersection(cur_v)
            
            if common:
                priority = 1.0 if col == '騎手' else 0.2
                c_text = ','.join(map(str, sorted(list(common))))
                
                for _, row in group.iterrows():
                    key = (row['場名'], row['R'], row['正番'])
                    if key in res_map:
                        res_map[key]['タイプ'].append(f'★{col}青塗')
                        res_map[key]['パターン'].append('青')
                        res_map[key]['条件'].append(f'共通({c_text})')
                        res_map[key]['スコア'] += 9.0 + priority
                        
                        # 青塗リストに追加（隣の判定用）
                        blue_horses.append({
                            '場名': row['場名'], 'R': row['R'], '正番': row['正番'],
                            '属性': f"{col}:{name}", '単ｵｯｽﾞ': row.get('単ｵｯｽﾞ')
                        })

    # --- B. 青塗の隣 (Logic B) ---
    for b in blue_horses:
        # 隣の馬番 (±1)
        for target_num in [b['正番'] - 1, b['正番'] + 1]:
            key = (b['場名'], b['R'], target_num)
            if key in res_map:
                target = res_map[key]
                # 自分自身が青塗でない、または別の青塗である場合も隣として評価
                
                n_score = 9.0
                # オッズ逆転チェック
                b_odds = b['単ｵｯｽﾞ']
                t_odds = target.get('単ｵｯｽﾞ')
                
                # オッズがあり、かつ 隣(target) < 青(blue) なら逆転加点
                is_reverse = False
                if pd.notna(b_odds) and pd.notna(t_odds):
                    if t_odds < b_odds:
                        n_score += 2.0
                        is_reverse = True
                
                target['タイプ'].append('△青塗隣' + ('(逆転)' if is_reverse else ''))
                target['パターン'].append('青隣')
                target['条件'].append(f"#{b['正番']}({b['属性']})の隣")
                target['スコア'] += n_score

    # --- C. ペア分析 (Logic C) ---
    pair_labels = list("ABCDEFGHIJKLMNOP")
    for col in ['騎手', '厩舎', '馬主']:
        if col not in df.columns: continue
        for name, group in df.groupby(['場名', col] if col=='騎手' else col):
            if len(group) < 2 or not name: continue
            sorted_rows = group.sort_values('R').to_dict('records')
            
            for i in range(len(sorted_rows)-1):
                r1 = sorted_rows[i]
                r2 = sorted_rows[i+1]
                
                # 4つの数字の総当たり一致確認
                v1 = [r1[c] for c in ['正番', '逆番', '正循環', '逆循環']]
                v2 = [r2[c] for c in ['正番', '逆番', '正循環', '逆循環']]
                
                pats = []
                for x in range(4):
                    for y in range(4):
                        if v1[x] == v2[y] and v1[x] != 0:
                            pats.append(pair_labels[x*4+y])
                
                if pats:
                    p_str = ",".join(pats)
                    is_chance = any(x in pats for x in ['C','D','G','H'])
                    type_str = '◎チャンス' if is_chance else '○狙い目'
                    score_add = 4.0 if is_chance else 3.0
                    
                    # R1への書き込み
                    k1 = (r1['場名'], r1['R'], r1['正番'])
                    if k1 in res_map:
                        res_map[k1]['タイプ'].append(type_str)
                        res_map[k1]['パターン'].append(p_str)
                        res_map[k1]['条件'].append(f"ペア({r2['R']}R)")
                        res_map[k1]['スコア'] += score_add
                        
                    # R2への書き込み
                    k2 = (r2['場名'], r2['R'], r2['正番'])
                    if k2 in res_map:
                        res_map[k2]['タイプ'].append(type_str)
                        res_map[k2]['パターン'].append(p_str)
                        res_map[k2]['条件'].append(f"ペア({r1['R']}R)")
                        res_map[k2]['スコア'] += score_add

    # 結果をDataFrameに戻す
    final_df = pd.DataFrame(list(res_map.values()))
    
    # リストを文字列に変換して見やすく
    for c in ['タイプ', 'パターン', '条件']:
        final_df[c] = final_df[c].apply(lambda x: ' / '.join(sorted(set(x), key=x.index)) if x else '')
    
    return final_df

# --- 4. Webオッズ取得 ---
def fetch_odds(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = 'euc-jp' # netkeiba specific
        soup = BeautifulSoup(resp.content, 'html.parser')
        rows = soup.select('tr.HorseList')
        data = []
        for r in rows:
            u = r.select_one('td[class*="Umaban"]')
            o = r.select_one('td[class*="Popular"]')
            if u:
                u_n = u.get_text(strip=True)
                o_v = re.sub(r'\(.*?\)', '', o.get_text(strip=True)) if o else 'nan'
                try: dv = float(o_v)
                except: dv = np.nan
                data.append({'正番': int(u_n), '単ｵｯｽﾞ': dv})
        return pd.DataFrame(data) if data else None
    except: return None

# --- 5. UI構成 ---
st.title("🏇 配置馬券術 分析システム")

with st.sidebar:
    up_file = st.file_uploader("当日データをアップロード", type=['xlsx', 'csv'])
    if 'analyzed_df' in st.session_state:
        csv = st.session_state['analyzed_df'].to_csv(index=False).encode('utf-8-sig')
        st.download_button("💾 現在のデータを保存", csv, "race_result.csv")

if up_file:
    df_raw, status = load_data(up_file)
    if status == "success":
        # 初回分析
        if 'analyzed_df' not in st.session_state:
            st.session_state['analyzed_df'] = analyze_haichi(df_raw)

        full_df = st.session_state['analyzed_df']
        places = sorted(full_df['場名'].unique())
        p_tabs = st.tabs(places)
        
        for p_tab, place in zip(p_tabs, places):
            with p_tab:
                p_df = full_df[full_df['場名'] == place]
                r_list = sorted(p_df['R'].unique())
                r_tabs = st.tabs([f"{r}R" for r in r_list])
                for r_tab, r_num in zip(r_tabs, r_list):
                    with r_tab:
                        # オッズ更新
                        with st.expander("🌐 ネット競馬から最新オッズを取得"):
                            u_in = st.text_input("URLを貼り付け", key=f"u_{place}_{r_num}")
                            if st.button("オッズ更新実行", key=f"b_{place}_{r_num}"):
                                new_o = fetch_odds(u_in)
                                if new_o is not None:
                                    # データフレームを更新
                                    curr_df = st.session_state['analyzed_df']
                                    for _, row in new_o.iterrows():
                                        mask = (curr_df['場名']==place) & (curr_df['R']==r_num) & (curr_df['正番']==row['正番'])
                                        curr_df.loc[mask, '単ｵｯｽﾞ'] = row['単ｵｯｽﾞ']
                                    
                                    # ★重要: オッズ更新後に再分析を実行（青塗隣の逆転判定などのため）
                                    st.session_state['analyzed_df'] = analyze_haichi(curr_df)
                                    st.success("更新完了！再計算しました。")
                                    st.rerun()
                                else:
                                    st.error("取得失敗。URLを確認してください。")

                        # データ表示（全頭表示 + スコア順ではない、馬番順）
                        disp_df = st.session_state['analyzed_df']
                        disp_df = disp_df[(disp_df['場名']==place) & (disp_df['R']==r_num)].sort_values('正番')
                        
                        # ハイライト機能
                        def highlight_row(row):
                            styles = [''] * len(row)
                            if row['スコア'] >= 10: # 高得点
                                return ['background-color: #ffcccc'] * len(row)
                            elif '青' in str(row['タイプ']): # 青塗関連
                                return ['background-color: #e6f3ff'] * len(row)
                            return styles

                        st.dataframe(
                            disp_df[['正番', '馬名', '騎手', '単ｵｯｽﾞ', 'タイプ', 'パターン', '条件', 'スコア']]
                            .style.apply(highlight_row, axis=1),
                            use_container_width=True,
                            hide_index=True
                        )

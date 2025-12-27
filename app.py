import streamlit as st
import pandas as pd
import numpy as np
import re
import plotly.express as px
import openpyxl
import requests
from bs4 import BeautifulSoup

# ページ設定
st.set_page_config(page_title="配置馬券術 Web", layout="wide")

# ==========================================
# 1. 共通ロジック & データ読み込み
# ==========================================

def to_half_width(text):
    if isinstance(text, (list, pd.Series, np.ndarray)):
        text = str(text)
    if pd.isna(text): return text
    text = str(text)
    table = str.maketrans('０１２３４５６７８９', '0123456789')
    text = text.translate(table)
    text = re.sub(r'[^\d\.]', '', text)
    return text

def normalize_name(x):
    if pd.isna(x): return ''
    normalized_name = str(x).strip().replace('　', '').replace(' ', '')
    normalized_name = re.sub(r'[★☆▲△◇]', '', normalized_name)
    if ',' in normalized_name: normalized_name = normalized_name.split(',')[0]
    text = re.sub(r'[0-9\.]+[Rr]', '', normalized_name)
    text = re.sub(r'\(.*?\)', '', text)
    return text.replace('/', '').strip()

@st.cache_data
def load_data(file):
    df = None
    if file.name.endswith('.xlsx'):
        try:
            file.seek(0)
            df = pd.read_excel(file, engine='openpyxl')
        except Exception as e:
            return pd.DataFrame(), f"Excel読み込みエラー: {e}"
    else:
        try:
            file.seek(0)
            df = pd.read_csv(file, encoding='utf-8', on_bad_lines='skip')
        except UnicodeDecodeError:
            try:
                file.seek(0)
                df = pd.read_csv(file, encoding='cp932', on_bad_lines='skip')
            except Exception as e:
                return pd.DataFrame(), f"CSV読み込みエラー: {e}"
        except Exception as e:
            return pd.DataFrame(), f"CSV予期せぬエラー: {e}"

    df.columns = df.columns.str.strip()
    rename_map = {
        '場所': '場名', '開催': '場名', 
        '調教師': '厩舎', '調教師名': '厩舎', '厩舎名': '厩舎',
        '騎手名': '騎手',
        'レース': 'R', 'Ｒ': 'R', 'レース名': 'R',
        '着': '着順', '着 順': '着順', '番': '正番', '馬番': '正番',
        '単オッズ': '単ｵｯｽﾞ', '単勝オッズ': '単ｵｯｽﾞ', 'オッズ': '単ｵｯｽﾞ', '単勝': '単ｵｯｽﾞ', '単': '単ｵｯｽﾞ'
    }
    df = df.rename(columns=rename_map)
    df = df.loc[:, ~df.columns.duplicated()]

    if '場名' not in df.columns: df['場名'] = 'Unknown'

    target_numeric_cols = ['R', '正番', '単ｵｯｽﾞ', '逆番', '正循環', '逆循環', '頭数']
    for col in target_numeric_cols:
        if col in df.columns:
            df[col] = df[col].apply(to_half_width)
            df[col] = pd.to_numeric(df[col], errors='coerce')

    if 'R' not in df.columns or '正番' not in df.columns:
        return pd.DataFrame(), "エラー: 必須列が見つかりません。"

    df = df.dropna(subset=['R', '正番'])
    df['R'] = df['R'].astype(int); df['正番'] = df['正番'].astype(int)

    for col in ['騎手', '厩舎', '馬主']:
        if col in df.columns: df[col] = df[col].apply(normalize_name)
        else: df[col] = ''
            
    required_cols = ['R', '場名', '馬名', '正番', '騎手', '厩舎', '馬主', '単ｵｯｽﾞ', '逆番', '正循環', '逆循環', '頭数']
    for col in required_cols:
        if col not in df.columns: df[col] = np.nan

    save_cols = ['属性', 'タイプ', 'パターン', '条件', 'スコア', '着順', '傾向加点', '総合スコア']
    existing_save_cols = [c for c in save_cols if c in df.columns]
    
    return df[required_cols + existing_save_cols].copy(), "success"

# ★修正: HTML構造を直接解析する最強のWeb取得関数
def fetch_odds_from_web(url, force_mode=False):
    try:
        t_url = url
        if not force_mode and "shutuba.html" in t_url:
            t_url = t_url.replace("shutuba.html", "odds.html")

        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        response = requests.get(t_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # 文字コード強制デコード
        if "netkeiba" in t_url: content = response.content.decode('euc-jp', errors='ignore')
        else: content = response.text

        soup = BeautifulSoup(content, 'html.parser')
        
        # ネット競馬の表の行（HorseListクラス）を探す
        rows = soup.select('tr.HorseList')
        
        # 救済措置: もしodds.htmlで取れなかったらshutuba.html（元のURL）で再トライ
        if not rows and t_url != url:
            response = requests.get(url, headers=headers, timeout=15)
            content = response.content.decode('euc-jp', errors='ignore')
            soup = BeautifulSoup(content, 'html.parser')
            rows = soup.select('tr.HorseList')

        if not rows:
            return None, "対象の馬リストが見つかりませんでした。"

        data = []
        for row in rows:
            # 馬番の抽出 (Umabanクラス)
            umaban_td = row.select_one('td[class*="Umaban"]')
            # オッズの抽出 (Popularクラス)
            odds_td = row.select_one('td[class*="Popular"]')
            
            if umaban_td:
                u_num = umaban_td.get_text(strip=True)
                o_val = np.nan
                if odds_td:
                    # spanの中の数値、またはテキストを直接取得
                    o_text = odds_td.get_text(strip=True)
                    # 余計な文字（カッコ書きなど）を除去
                    o_text = re.sub(r'\(.*?\)', '', o_text)
                    try: o_val = float(o_text)
                    except: o_val = np.nan
                
                data.append({'正番': int(u_num), '単ｵｯｽﾞ': o_val})

        if data:
            res_df = pd.DataFrame(data)
            return res_df, "Success"
        return None, "データ解析に失敗しました。"

    except Exception as e:
        return None, str(e)

# ==========================================
# 2. 配置計算・分析ロジック
# ==========================================

def calc_haichi_numbers(df):
    max_umaban = df.groupby(['場名', 'R'])['正番'].transform('max')
    df['使用頭数'] = max_umaban.fillna(16).astype(int)
    if '頭数' in df.columns: df['使用頭数'] = df['頭数'].fillna(df['使用頭数']).astype(int)
    df['使用頭数'] = np.maximum(df['使用頭数'], df['正番'])
    def calc(row):
        t=int(row['使用頭数']); s=int(row['正番']); g=(t+1)-s; sj=t+s; gj=t+g
        return pd.Series([g, sj, gj])
    df[['計算_逆番', '計算_正循環', '計算_逆循環']] = df.apply(calc, axis=1)
    return df

def get_pair_pattern(row1, row2):
    r1 = [row1['正番'], row1['計算_逆番'], row1['計算_正循環'], row1['計算_逆循環']]
    r2 = [row2['正番'], row2['計算_逆番'], row2['計算_正循環'], row2['計算_逆循環']]
    label = list("ABCDEFGHIJKLMNOP")
    pairs = [label[i*4+j] for i in range(4) for j in range(4) if r1[i]==r2[j] and r1[i]!=0]
    return ",".join(pairs)

def get_common_values(group):
    cols = ['正番', '計算_逆番', '計算_正循環', '計算_逆循環']
    common_set = None
    for _, row in group.iterrows():
        cur = set()
        for c in cols:
            val = row.get(c)
            if pd.notna(val): cur.add(int(val))
        if common_set is None: common_set = cur
        else: common_set = common_set.intersection(cur)
    if common_set: return ','.join(map(str, sorted(list(common_set))))
    return None

def analyze_logic(df_curr, df_prev=None):
    df_curr = calc_haichi_numbers(df_curr)
    if df_prev is not None and not df_prev.empty: df_prev = calc_haichi_numbers(df_prev)
    rec_list = []
    
    blue_keys = set()
    for col in ['騎手', '厩舎', '馬主']:
        if col not in df_curr.columns: continue
        group_keys = ['場名', col] if col == '騎手' else [col]
        for name_key, group in df_curr.groupby(group_keys):
            if len(group) < 2: continue
            target_name = name_key[1] if col == '騎手' else name_key
            if not target_name: continue
            common_vals = get_common_values(group)
            if common_vals:
                priority = 1.0 if col == '騎手' else 0.2
                for _, row in group.iterrows():
                    rec_list.append({
                        '場名': row['場名'], 'R': row['R'], '正番': row['正番'], '馬名': row['馬名'],
                        '単ｵｯｽﾞ': row.get('単ｵｯｽﾞ'), '属性': f"{col}:{target_name}", 
                        'タイプ': f'★ {col}青塗', 'パターン': '青', '条件': f'共通値({common_vals})', 'スコア': 9.0 + priority
                    })
                    blue_keys.add((row['場名'], row['R'], row['馬名'], f"{col}:{target_name}"))

    if blue_keys:
        blue_lookup = {}
        for b in blue_keys:
            key = (b[0], b[1])
            if key not in blue_lookup: blue_lookup[key] = []
            blue_lookup[key].append({'馬名': b[2], '属性': b[3]})
        for (place, race), group in df_curr.groupby(['場名', 'R']):
            if (place, race) not in blue_lookup: continue
            group = group.sort_values('正番')
            umaban_map = {int(row['正番']): row for _, row in group.iterrows()}
            for b_info in blue_lookup[(place, race)]:
                b_row = group[group['馬名'] == b_info['馬名']]
                if b_row.empty: continue
                b_row = b_row.iloc[0]; curr_num = int(b_row['正番'])
                for t_num in [curr_num - 1, curr_num + 1]:
                    if t_num in umaban_map:
                        t_row = umaban_map[t_num]
                        neighbor_score = 9.0
                        b_odds = pd.to_numeric(b_row.get('単ｵｯｽﾞ'), errors='coerce')
                        n_odds = pd.to_numeric(t_row.get('単ｵｯｽﾞ'), errors='coerce')
                        if pd.notna(b_odds) and pd.notna(n_odds) and n_odds < b_odds: neighbor_score += 2.0
                        rec_list.append({
                            '場名': place, 'R': race, '正番': t_num, '馬名': t_row['馬名'],
                            '単ｵｯｽﾞ': n_odds, '属性': f"(青塗隣) <{b_info['属性']}>", 
                            'タイプ': '△ 青塗の隣', 'パターン': '青隣', '条件': f"#{curr_num}の隣", 'スコア': neighbor_score
                        })

    for col in ['騎手', '厩舎', '馬主']:
        if col not in df_curr.columns: continue
        for name, group in df_curr.groupby(['場名', col] if col=='騎手' else col):
            if len(group) < 2: continue
            group = group.sort_values('R').to_dict('records')
            for i in range(len(group)-1):
                curr, nxt = group[i], group[i+1]
                pat = get_pair_pattern(curr, nxt)
                if pat:
                    lbl = "◎ チャンス" if any(x in pat for x in ['C','D','G','H']) else "○ 狙い目"
                    base = 4.0 if lbl.startswith("◎") else 3.0
                    rec_list.append({'場名': curr['場名'], 'R': curr['R'], '正番': curr['正番'], '馬名': curr['馬名'], '単ｵｯｽﾞ': curr.get('単ｵｯｽﾞ'), '属性': f"{col}:{name}", 'タイプ': lbl, 'パターン': pat, '条件': f'ペア({nxt["R"]}R)', 'スコア': base + 1.0})
                    rec_list.append({'場名': nxt['場名'], 'R': nxt['R'], '正番': nxt['正番'], '馬名': nxt['馬名'], '単ｵｯｽﾞ': nxt.get('単ｵｯｽﾞ'), '属性': f"{col}:{name}", 'タイプ': lbl, 'パターン': pat, '条件': f'ペア({curr["R"]}R)', 'スコア': base + 1.0})

    if not rec_list: return pd.DataFrame()
    res_df = pd.DataFrame(rec_list)
    agg_funcs = {'単ｵｯｽﾞ': 'min', '属性': lambda x: ' + '.join(sorted(set(x))), 'タイプ': lambda x: ' / '.join(sorted(set(x))), 'パターン': lambda x: ','.join(sorted(set(x))), '条件': lambda x: ' / '.join(sorted(set(x))), 'スコア': 'sum', '正番': 'first'}
    res_df = res_df.groupby(['場名', 'R', '馬名'], as_index=False).agg(agg_funcs)
    if '着順' not in res_df.columns: res_df['着順'] = np.nan
    return res_df

# ==========================================
# 3. 判定ロジック (総合スコア重視)
# ==========================================

def apply_ranking_logic(df_in):
    if df_in.empty: return df_in
    df = df_in.copy()
    df['着順'] = pd.to_numeric(df['着順'], errors='coerce')
    df_hits = df[df['着順'] <= 3]
    hit_patterns = set(); downgraded_attrs = set()
    for _, row in df_hits.iterrows():
        hit_patterns.update(str(row.get('パターン', '')).split(','))
        if '青隣' in str(row.get('パターン', '')):
            found = re.findall(r'<(.*?)>', str(row.get('属性', '')))
            downgraded_attrs.update(found)

    def calc_bonus(row):
        bonus = 0.0; pats = str(row.get('パターン', '')).split(',')
        for p in pats:
            if p in hit_patterns and len(p) == 1: bonus += 4.0
        if '青' in pats:
            my_attrs = str(row.get('属性', ''))
            if any(bad in my_attrs for bad in downgraded_attrs): bonus -= 3.0
        odds = pd.to_numeric(row.get('単ｵｯｽﾞ'), errors='coerce')
        if pd.notna(odds) and odds > 49.9: bonus -= 30.0
        return bonus

    def get_bet_recommendation(row):
        score = row['総合スコア']; rank = row['レース内順位']; pats = str(row.get('パターン', '')).split(',')
        is_trend = any(p in hit_patterns for p in pats)
        if score >= 15: r_label = "S"
        elif score >= 12: r_label = "A"
        elif score >= 10: r_label = "B"
        elif '青' in pats: r_label = "C"
        else: r_label = "D"
        if rank > 1:
            if r_label == "S": r_label = "A"
            elif r_label == "A": r_label = "B"
        if r_label == "S": return "👑 盤石の軸" if is_trend else "👑 鉄板級"
        if r_label == "A": return "✨ 傾向軸" if is_trend else "◎ 軸候補"
        if r_label == "B": return "🔥 激熱相手" if is_trend else "○ 相手筆頭"
        if r_label == "C": return "★ 傾向穴" if is_trend else "▲ 青塗穴"
        return "注 傾向合致" if is_trend else "△ 紐"

    df['傾向加点'] = df.apply(calc_bonus, axis=1)
    df['総合スコア'] = df['スコア'].fillna(0) + df['傾向加点'].fillna(0)
    df['レース内順位'] = df.groupby(['場名', 'R'])['総合スコア'].rank(method='min', ascending=False)
    df['推奨買い目'] = df.apply(get_bet_recommendation, axis=1)
    return df

# ==========================================
# 4. 画面表示
# ==========================================

st.title("🏇 配置馬券術 分析ツール")

with st.sidebar:
    st.header("1. データ入力")
    uploaded_file = st.file_uploader("当日データをアップロード", type=['xlsx', 'csv'])
    prev_file = st.file_uploader("前日データをアップロード", type=['xlsx', 'csv'])
    if 'analyzed_df' in st.session_state and not st.session_state['analyzed_df'].empty:
        csv = st.session_state['analyzed_df'].to_csv(index=False).encode('utf-8-sig')
        st.download_button("💾 保存 (CSV)", data=csv, file_name="race_save.csv", mime="text/csv")

if uploaded_file:
    df_raw, status = load_data(uploaded_file)
    df_prev, _ = load_data(prev_file) if prev_file else (None, None)
    
    if status != "success": st.error(status)
    else:
        if 'analyzed_df' not in st.session_state:
            with st.spinner('全レース分析中...'):
                res = analyze_logic(df_raw, df_prev)
                st.session_state['analyzed_df'] = apply_ranking_logic(res)

        if not st.session_state['analyzed_df'].empty:
            full_df = st.session_state['analyzed_df']
            places = sorted(full_df['場名'].unique())
            
            with st.form("main_form"):
                p_tabs = st.tabs(places)
                edited_list = []
                for p_tab, place in zip(p_tabs, places):
                    with p_tab:
                        p_df = full_df[full_df['場名'] == place]
                        r_list = sorted(p_df['R'].unique())
                        r_tabs = st.tabs([f"{r}R" for r in r_list])
                        for r_tab, r_num in zip(r_tabs, r_list):
                            with r_tab:
                                with st.expander(f"🌐 {place}{r_num}R オッズ取得"):
                                    c1, c2 = st.columns([3,1])
                                    u_in = c1.text_input("URL", key=f"u_{place}_{r_num}")
                                    f_mo = c2.checkbox("固定", key=f"f_{place}_{r_num}")
                                    if st.form_submit_button(f"📥 {place}{r_num}R 更新"):
                                        if u_in:
                                            new_odds, msg = fetch_odds_from_web(u_in, f_mo)
                                            if new_odds is not None:
                                                for _, o_row in new_odds.iterrows():
                                                    mask = (st.session_state['analyzed_df']['場名'] == place) & (st.session_state['analyzed_df']['R'] == r_num) & (st.session_state['analyzed_df']['正番'] == o_row['正番'])
                                                    st.session_state['analyzed_df'].loc[mask, '単ｵｯｽﾞ'] = o_row['単ｵｯｽﾞ']
                                                # 再計算
                                                st.session_state['analyzed_df'] = apply_ranking_logic(st.session_state['analyzed_df'])
                                                st.success("オッズを更新しました！")
                                                st.rerun()
                                            else: st.error(f"取得失敗: {msg}")

                                r_df = p_df[p_df['R'] == r_num]
                                ed = st.data_editor(r_df, disabled=[c for c in r_df.columns if c != '着順'], hide_index=True, use_container_width=True, key=f"ed_{place}_{r_num}")
                                edited_list.append(ed)
                if st.form_submit_button("🔄 全レース確定して再計算"):
                    combined = pd.concat(edited_list, ignore_index=True)
                    st.session_state['analyzed_df'] = apply_ranking_logic(combined)
                    st.rerun()

            st.divider()
            st.subheader("📈 推奨馬リスト")
            future = st.session_state['analyzed_df'][st.session_state['analyzed_df']['着順'].isna()]
            if not future.empty:
                f_places = sorted(future['場名'].unique())
                f_tabs = st.tabs(f_places)
                for tab, place in zip(f_tabs, f_places):
                    with tab:
                        p_future = future[future['場名'] == place]
                        f_r_list = sorted(p_future['R'].unique())
                        fr_tabs = st.tabs([f"{r}R" for r in f_r_list])
                        for fr_tab, r_num in zip(fr_tabs, f_r_list):
                            with fr_tab:
                                t_df = p_future[p_future['R'] == r_num].sort_values('総合スコア', ascending=False)
                                if not t_df.empty:
                                    top = t_df.iloc[0]
                                    if top['総合スコア'] >= 15: st.success(f"🔥 **盤石**: {top['正番']} {top['馬名']} ({top['総合スコア']:.1f})")
                                    elif top['総合スコア'] >= 12: st.info(f"💡 **推奨**: {top['正番']} {top['馬名']} ({top['総合スコア']:.1f})")
                                st.dataframe(t_df[['正番', '馬名', '単ｵｯｽﾞ', 'タイプ', '総合スコア', '推奨買い目']], hide_index=True, use_container_width=True)

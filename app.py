import streamlit as st
import pandas as pd
import numpy as np
import re
import plotly.express as px
import openpyxl
import requests

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
    """ファイルを読み込み、エラーハンドリングを行う"""
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

    # データ整形
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
        return pd.DataFrame(), "エラー: 必須列（レース名/R、馬番/正番）が見つかりません。"

    df = df.dropna(subset=['R', '正番'])
    df['R'] = df['R'].astype(int)
    df['正番'] = df['正番'].astype(int)

    for col in ['騎手', '厩舎', '馬主']:
        if col in df.columns:
            df[col] = df[col].apply(normalize_name)
        else:
            df[col] = ''
            
    required_cols = ['R', '場名', '馬名', '正番', '騎手', '厩舎', '馬主', '単ｵｯｽﾞ', '逆番', '正循環', '逆循環', '頭数']
    for col in required_cols:
        if col not in df.columns:
            df[col] = np.nan

    save_cols = ['属性', 'タイプ', 'パターン', '条件', 'スコア', '着順', '傾向加点', '総合スコア']
    existing_save_cols = [c for c in save_cols if c in df.columns]
    
    return df[required_cols + existing_save_cols].copy(), "success"

# ★修正: Webからオッズを取得する関数 (BeautifulSoupを使用)
def fetch_odds_from_web(url):
    """
    指定されたURLからテーブルを読み込み、馬番と単勝オッズのペアを返す
    """
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        # 文字コードの自動判定補正
        if response.encoding == 'ISO-8859-1':
            response.encoding = response.apparent_encoding

        # ★変更点: bs4 または html5lib を使用して読み込む（lxmlは使わない）
        # これにより依存関係のエラーを回避しやすくする
        try:
            dfs = pd.read_html(response.text, flavor='bs4')
        except ImportError:
            try:
                dfs = pd.read_html(response.text, flavor='html5lib')
            except ImportError:
                # 何も指定せずpandasにお任せ
                dfs = pd.read_html(response.text)
        
        target_df = None
        for df in dfs:
            # カラム名のクリーニング
            cols = [str(c).replace(' ', '').replace('\n', '') for c in df.columns]
            
            # マルチインデックス対応
            if isinstance(df.columns, pd.MultiIndex):
                flat_cols = []
                for c in df.columns:
                    flat_cols.append(''.join([str(x) for x in c if 'Unnamed' not in str(x)]))
                cols = flat_cols
                df.columns = cols

            # 「馬番」と「単勝」が含まれるテーブルを探す
            if any('馬番' in c for c in cols) and any('単勝' in c for c in cols):
                col_map = {}
                for c, original_c in zip(cols, df.columns):
                    if '馬番' in c: col_map[original_c] = '正番'
                    elif '単勝' in c and 'オッズ' in c: col_map[original_c] = '単ｵｯｽﾞ'
                    elif '単勝' in c: col_map[original_c] = '単ｵｯｽﾞ'
                
                if '正番' in col_map.values() and '単ｵｯｽﾞ' in col_map.values():
                    target_df = df.rename(columns=col_map)
                    break
        
        if target_df is not None:
            res = target_df[['正番', '単ｵｯｽﾞ']].copy()
            res['正番'] = pd.to_numeric(res['正番'], errors='coerce')
            
            def clean_odds(x):
                try: return float(x)
                except: return np.nan
            
            res['単ｵｯｽﾞ'] = res['単ｵｯｽﾞ'].apply(clean_odds)
            res = res.dropna(subset=['正番'])
            return res
        else:
            st.error("ページ内にオッズ表が見つかりませんでした。URLが正しいか確認してください（netkeibaの出馬表ページなどを推奨）。")
            return None

    except Exception as e:
        st.error(f"システムエラー: {e}")
        return None

# ==========================================
# 2. 配置計算・分析ロジック
# ==========================================

def calc_haichi_numbers(df):
    check_cols = ['逆番', '正循環', '逆循環']
    if set(check_cols).issubset(df.columns) and df[check_cols].notna().all().all():
        df['計算_逆番'] = df['逆番']
        df['計算_正循環'] = df['正循環']
        df['計算_逆循環'] = df['逆循環']
        return df
    
    max_umaban = df.groupby(['場名', 'R'])['正番'].transform('max')
    df['使用頭数'] = max_umaban.fillna(16).astype(int)
    if '頭数' in df.columns:
        df['使用頭数'] = df['頭数'].fillna(df['使用頭数']).astype(int)
    df['使用頭数'] = np.maximum(df['使用頭数'], df['正番'])
    
    def calc(row):
        t = int(row['使用頭数'])
        s = int(row['正番'])
        g = (t + 1) - s
        sj = t + s
        gj = t + g
        return pd.Series([g, sj, gj])
    
    df[['計算_逆番', '計算_正循環', '計算_逆循環']] = df.apply(calc, axis=1)
    return df

def get_pair_pattern(row1, row2):
    def val(x):
        try: return int(float(x)) 
        except: return None
    r1 = [val(row1['正番']), val(row1['計算_逆番']), val(row1['計算_正循環']), val(row1['計算_逆循環'])]
    r2 = [val(row2['正番']), val(row2['計算_逆番']), val(row2['計算_正循環']), val(row2['計算_逆循環'])]
    label = list("ABCDEFGHIJKLMNOP")
    pairs = [label[i * 4 + j] for i in range(4) for j in range(4)
             if r1[i] is not None and r2[j] is not None and r1[i] == r2[j] and r1[i] != 0]
    return ",".join(pairs)

def get_common_values(group):
    cols = ['正番', '計算_逆番', '計算_正循環', '計算_逆循環']
    common_set = None
    for _, row in group.iterrows():
        current_set = set()
        for col in cols:
            val = row.get(col)
            if pd.notna(val):
                try:
                    num = int(float(val))
                    if num != 0: current_set.add(num)
                except: continue
        if common_set is None: common_set = current_set
        else: common_set = common_set.intersection(current_set)
        if not common_set: return None
    if common_set: return ','.join(map(str, sorted(list(common_set))))
    return None

def analyze_logic(df_curr, df_prev=None):
    df_curr = calc_haichi_numbers(df_curr)
    if df_prev is not None and not df_prev.empty:
        df_prev = calc_haichi_numbers(df_prev)
    
    rec_list = []
    
    # A. 青塗
    blue_keys = set()
    for col in ['騎手', '厩舎', '馬主']:
        if col not in df_curr.columns: continue
        if col == '騎手': group_keys = ['場名', col]
        else: group_keys = [col]
        try:
            for name_key, group in df_curr.groupby(group_keys):
                if len(group) < 2: continue
                target_name = name_key[1] if col == '騎手' else name_key
                if not target_name: continue
                common_vals = get_common_values(group)
                if common_vals:
                    all_races_display = [f"{r['場名']}{r['R']}" for _, r in group.iterrows()]
                    priority = 1.0 if col == '騎手' else 0.2
                    for _, row in group.iterrows():
                        current_race_str = f"{row['場名']}{row['R']}"
                        other_races = [s for s in all_races_display if s != current_race_str]
                        other_races = sorted(list(set(other_races)))
                        remark = f'[{col}] 共通値({common_vals}) [他:{",".join(other_races)}]'
                        odds_val = row.get('単ｵｯｽﾞ', np.nan)
                        rec_list.append({
                            '場名': row['場名'], 'R': row['R'], '正番': row['正番'], '馬名': row['馬名'],
                            '単ｵｯｽﾞ': odds_val,
                            '属性': f"{col}:{target_name}", 
                            'タイプ': f'★ {col}青塗', 
                            'パターン': '青', 
                            '条件': remark,
                            'スコア': 9.0 + priority
                        })
                        blue_keys.add((row['場名'], row['R'], row['馬名'], row['属性']))
        except: continue

    # B. 青塗の隣
    if blue_keys:
        blue_lookup = {}
        for b in blue_keys:
            key = (b[0], b[1]) 
            if key not in blue_lookup: blue_lookup[key] = []
            blue_lookup[key].append({'馬名': b[2], '属性': b[3]})

        for (place, race), group in df_curr.groupby(['場名', 'R']):
            key = (place, race)
            if key not in blue_lookup: continue
            blue_horses_info = blue_lookup[key]
            group = group.sort_values('正番')
            umaban_map = {int(row['正番']): row for _, row in group.iterrows()}
            blue_horse_names = [b['馬名'] for b in blue_horses_info]

            for b_info in blue_horses_info:
                b_row = group[group['馬名'] == b_info['馬名']]
                if b_row.empty: continue
                b_row = b_row.iloc[0]
                curr_num = int(b_row['正番'])
                source_attr = b_info['属性']
                blue_odds = pd.to_numeric(b_row.get('単ｵｯｽﾞ'), errors='coerce')
                
                for t_num in [curr_num - 1, curr_num + 1]:
                    if t_num in umaban_map:
                        t_row = umaban_map[t_num]
                        if t_row['馬名'] not in blue_horse_names:
                            neighbor_odds = pd.to_numeric(t_row.get('単ｵｯｽﾞ'), errors='coerce')
                            neighbor_score = 9.0
                            
                            # 隣のオッズ < 本体のオッズ ならスコア加算 (逆転)
                            if pd.notna(blue_odds) and pd.notna(neighbor_odds):
                                if neighbor_odds < blue_odds:
                                    neighbor_score += 2.0
                            
                            rec_list.append({
                                '場名': place, 'R': race, '正番': t_num, '馬名': t_row['馬名'],
                                '単ｵｯｽﾞ': neighbor_odds,
                                '属性': f"(青塗隣) <{source_attr}>", 
                                'タイプ': '△ 青塗の隣',
                                'パターン': '青隣',
                                '条件': f"青塗#{curr_num}({source_attr})の隣",
                                'スコア': neighbor_score
                            })

    # C. 通常ペア (騎手)
    if '騎手' in df_curr.columns:
        for (place, name), group in df_curr.groupby(['場名', '騎手']):
            if len(group) < 2: continue
            group = group.sort_values('R').to_dict('records')
            for i in range(len(group)-1):
                curr, next_r = group[i], group[i+1]
                pat = get_pair_pattern(curr, next_r)
                if pat:
                    label = "◎ チャンス" if any(x in pat for x in ['C','D','G','H']) else "○ 狙い目"
                    base_score = 4.0 if label.startswith("◎") else 3.0
                    rec_list.append({
                        '場名': curr['場名'], 'R': curr['R'], '正番': curr['正番'], '馬名': curr['馬名'],
                        '単ｵｯｽﾞ': curr.get('単ｵｯｽﾞ', np.nan),
                        '属性': f"騎手:{name}", 'タイプ': label, 'パターン': pat, 
                        '条件': f"[騎手] ペア({next_r['R']}R #{next_r['正番']})", 'スコア': base_score + 1.0
                    })
                    rec_list.append({
                        '場名': next_r['場名'], 'R': next_r['R'], '正番': next_r['正番'], '馬名': next_r['馬名'],
                        '単ｵｯｽﾞ': next_r.get('単ｵｯｽﾞ', np.nan),
                        '属性': f"騎手:{name}", 'タイプ': label, 'パターン': pat, 
                        '条件': f"[騎手] ペア({curr['R']}R #{curr['正番']})", 'スコア': base_score + 1.0
                    })

    # C. 通常ペア (厩舎・馬主)
    for col in ['厩舎', '馬主']:
        if col not in df_curr.columns: continue
        for name, group in df_curr.groupby(col):
            if len(group) < 2: continue
            group = group.sort_values(['R', '場名']).to_dict('records')
            for i in range(len(group)-1):
                curr, next_r = group[i], group[i+1]
                pat = get_pair_pattern(curr, next_r)
                if pat:
                    label = "◎ チャンス" if any(x in pat for x in ['C','D','G','H']) else "○ 狙い目"
                    base_score = 4.0 if label.startswith("◎") else 3.0
                    cond_curr = f"[{col}] ペア({next_r['場名']}{next_r['R']}R #{next_r['正番']})"
                    cond_next = f"[{col}] ペア({curr['場名']}{curr['R']}R #{curr['正番']})"
                    bonus = 0.2
                    rec_list.append({
                        '場名': curr['場名'], 'R': curr['R'], '正番': curr['正番'], '馬名': curr['馬名'],
                        '単ｵｯｽﾞ': curr.get('単ｵｯｽﾞ', np.nan),
                        '属性': f"{col}:{name}", 'タイプ': label, 'パターン': pat, 
                        '条件': cond_curr, 'スコア': base_score + bonus
                    })
                    rec_list.append({
                        '場名': next_r['場名'], 'R': next_r['R'], '正番': next_r['正番'], '馬名': next_r['馬名'],
                        '単ｵｯｽﾞ': next_r.get('単ｵｯｽﾞ', np.nan),
                        '属性': f"{col}:{name}", 'タイプ': label, 'パターン': pat, 
                        '条件': cond_next, 'スコア': base_score + bonus
                    })

    # D. 前日同配置
    if df_prev is not None and not df_prev.empty:
        for idx, row in df_curr.iterrows():
            race = row['R']
            name = row['騎手']
            if not name: continue
            prev_rows = df_prev[(df_prev['場名'] == row['場名']) & (df_prev['R'] == race) & (df_prev['騎手'] == name)]
            for _, p_row in prev_rows.iterrows():
                is_seiban = (p_row['正番'] == row['正番'])
                is_gyaku = (p_row['計算_逆番'] == row['計算_逆番'])
                if is_seiban or is_gyaku:
                    reason = "正番" if is_seiban else "逆番"
                    prev_rank = pd.to_numeric(p_row.get('着順'), errors='coerce')
                    condition_text = f"[騎手] 前日{race}R同配置({reason})"
                    if pd.notna(prev_rank):
                        if prev_rank > 3: condition_text += " <⚠️前日凡走>"
                        else: condition_text += " <✨前日好走>"
                    
                    rec_list.append({
                        '場名': row['場名'], 'R': race, '正番': row['正番'], '馬名': row['馬名'],
                        '単ｵｯｽﾞ': row.get('単ｵｯｽﾞ', np.nan),
                        '属性': f"騎手:{name}", 'タイプ': '★ 前日同配置', 
                        'パターン': '前日',
                        '条件': condition_text, 
                        'スコア': 8.3
                    })

    if not rec_list:
        return pd.DataFrame()
        
    res_df = pd.DataFrame(rec_list)
    
    agg_funcs = {
        '単ｵｯｽﾞ': 'min',
        '属性': lambda x: ' + '.join(sorted(set(x))),
        'タイプ': lambda x: ' / '.join(sorted(set(x), key=lambda s: 0 if '★' in s else 1)), 
        'パターン': lambda x: ','.join(sorted(set(x))),
        '条件': lambda x: ' / '.join(sorted(set(x))),
        'スコア': 'sum',
        '正番': 'first'
    }
    
    res_df = res_df.groupby(['場名', 'R', '馬名'], as_index=False).agg(agg_funcs)
    res_df = res_df.sort_values(['場名', 'R', 'スコア'], ascending=[True, True, False])
    
    if '着順' not in res_df.columns: res_df['着順'] = np.nan
    
    return res_df

# ==========================================
# 3. 総合評価・再計算ロジック
# ==========================================

def apply_ranking_logic(df_in):
    """最新のオッズやトレンドに基づいてスコアと推奨度を再計算する"""
    if df_in.empty: return df_in
    df = df_in.copy()
    
    df['着順'] = pd.to_numeric(df['着順'], errors='coerce')
    df_hits = df[df['着順'] <= 3]
    
    hit_patterns = set()
    downgraded_attrs = set()
    
    for _, row in df_hits.iterrows():
        pats = str(row.get('パターン', '')).split(',')
        hit_patterns.update(pats)
        if '青隣' in str(row.get('パターン', '')):
            found = re.findall(r'<(.*?)>', str(row.get('属性', '')))
            downgraded_attrs.update(found)

    def calc_bonus(row):
        row_pat = row.get('パターン', '')
        if not row_pat or pd.isna(row_pat): return 0.0
        pats = str(row_pat).split(',')
        bonus = 0.0
        
        # 1. ヒットパターン加点 (トレンド) +4.0
        for p in pats:
            if p in hit_patterns and len(p) == 1: 
                bonus += 4.0 
        
        # 2. 青塗処理
        if '青' in pats:
            my_attrs = str(row.get('属性', ''))
            for bad_attr in downgraded_attrs:
                if bad_attr in my_attrs:
                    bonus -= 3.0
                    break
        
        # 3. 高オッズによる減点 (50倍以上は圏外)
        odds = pd.to_numeric(row.get('単ｵｯｽﾞ'), errors='coerce')
        if pd.notna(odds) and odds > 49.9:
            bonus -= 30.0
                
        return bonus

    def get_bet_recommendation(row):
        score = row['総合スコア']
        rank_in_race = row['レース内順位']
        pat_str = str(row.get('パターン', ''))
        my_pats = pat_str.split(',')
        matched = [p for p in my_pats if p in hit_patterns]
        is_trend_horse = len(matched) > 0
        is_blue = '青' in my_pats

        if score >= 15: rank = "S"
        elif score >= 12: rank = "A"
        elif score >= 10: rank = "B"
        elif is_blue: rank = "C"
        else: rank = "D"

        if rank_in_race > 1:
            if rank == "S": rank = "A"
            elif rank == "A": rank = "B"
        
        if rank == "S":
            return "👑 盤石の軸" if is_trend_horse else "👑 鉄板級"
        elif rank == "A":
            return "✨ 傾向軸" if is_trend_horse else "◎ 軸候補"
        elif rank == "B":
            return "🔥 激熱相手" if is_trend_horse else "○ 相手筆頭"
        elif rank == "C":
            return "★ 傾向合致穴" if is_trend_horse else "▲ 青塗穴"
        else: 
            if is_trend_horse: return "注 傾向合致"
            return "△ 紐"

    df['傾向加点'] = df.apply(calc_bonus, axis=1)
    df['総合スコア'] = df['スコア'] + df['傾向加点']
    df['レース内順位'] = df.groupby(['場名', 'R'])['総合スコア'].rank(method='min', ascending=False)
    df['推奨買い目'] = df.apply(get_bet_recommendation, axis=1)
    
    return df

# ==========================================
# 4. Webアプリ画面 (Streamlit)
# ==========================================

st.title("🏇 配置馬券術 リアルタイム分析")

# サイドバー
with st.sidebar:
    st.header("1. データ入力")
    uploaded_file = st.file_uploader("当日データをアップロード (または保存データ)", type=['xlsx', 'csv'])
    prev_file = st.file_uploader("前日データをアップロード (任意)", type=['xlsx', 'csv'])
    
    st.markdown("---")
    st.header("2. データの保存")
    st.caption("着順を入力した状態でここからCSVを保存し、次回読み込むと続きから再開できます。")
    
    if 'analyzed_df' in st.session_state and not st.session_state['analyzed_df'].empty:
        csv = st.session_state['analyzed_df'].to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="💾 現在の状態を保存 (CSV)",
            data=csv,
            file_name="race_progress_save.csv",
            mime="text/

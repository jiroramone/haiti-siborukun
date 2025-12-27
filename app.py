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

# ★修正: 列名のフラット化を行い、KeyErrorを完全に防ぐ関数
def fetch_odds_from_web(url, force_mode=False):
    
    def try_fetch(target_url):
        try:
            # User-Agent偽装
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            response = requests.get(target_url, headers=headers, timeout=15)
            response.raise_for_status()
            
            if "netkeiba" in target_url:
                response.encoding = 'euc-jp'
            else:
                response.encoding = response.apparent_encoding

            dfs = []
            for parser in ['bs4', 'lxml', 'html5lib']:
                try:
                    dfs = pd.read_html(response.text, flavor=parser)
                    if dfs: break
                except: continue
            
            if not dfs: return None, "No tables found"

            target_df = None
            debug_logs = []

            for i, df in enumerate(dfs):
                # ★重要: 列名の強制フラット化 (MultiIndex解消)
                # これにより、df.columns は必ず単純な文字列のリストになります
                flat_cols = []
                for c in df.columns:
                    if isinstance(c, tuple):
                        # タプルの中身を結合し、ゴミを除去
                        col_str = ''.join([str(x) for x in c if 'Unnamed' not in str(x)])
                    else:
                        col_str = str(c)
                    # スペースや改行を削除
                    col_str = col_str.replace(' ', '').replace('　', '').replace('\n', '').replace('\r', '')
                    flat_cols.append(col_str)
                
                # データフレームの列名を上書き
                df.columns = flat_cols
                
                debug_logs.append(f"Table {i} Cols: {flat_cols}")

                # --- 判定ロジック ---
                has_umaban = any('馬番' in c for c in flat_cols)
                has_odds_related = any(x in c for c in flat_cols for x in ['単勝', 'オッズ', '人気', '予想'])

                if has_umaban and has_odds_related:
                    # rename用のマッピング辞書作成
                    # ここではもう列名は単純な文字列になっているので、KeyErrorは起きない
                    rename_dict = {}
                    
                    for col_name in flat_cols:
                        if '馬番' in col_name:
                            rename_dict[col_name] = '正番'
                        
                        elif '単勝' in col_name: rename_dict[col_name] = '単ｵｯｽﾞ'
                        elif '予想オッズ' in col_name: rename_dict[col_name] = '単ｵｯｽﾞ'
                        elif 'オッズ' in col_name and '単ｵｯｽﾞ' not in rename_dict.values(): rename_dict[col_name] = '単ｵｯｽﾞ'
                        elif '人気' in col_name: rename_dict[col_name] = '人気_temp'

                    # リネーム実行
                    df = df.rename(columns=rename_dict)

                    # 必須列があるか確認
                    if '正番' in df.columns:
                        # オッズ列がない場合、人気列で代用を試みる
                        if '単ｵｯｽﾞ' not in df.columns and '人気_temp' in df.columns:
                            df = df.rename(columns={'人気_temp': '単ｵｯｽﾞ'})
                        
                        if '単ｵｯｽﾞ' in df.columns:
                            target_df = df
                            break
            
            if target_df is not None:
                # 必要な列だけ抽出 (確実に存在することを確認済み)
                res = target_df[['正番', '単ｵｯｽﾞ']].copy()
                
                res['正番'] = pd.to_numeric(res['正番'], errors='coerce')
                
                def clean_odds(x):
                    s = str(x).strip()
                    if s in ['--', '---', '取消', '除外', 'nan', 'NaN', 'None']:
                        return np.nan
                    s = re.sub(r'\(.*?\)', '', s) 
                    try: return float(s)
                    except: return np.nan
                
                res['単ｵｯｽﾞ'] = res['単ｵｯｽﾞ'].apply(clean_odds)
                res = res.dropna(subset=['正番'])
                
                if res['単ｵｯｽﾞ'].isna().all():
                    st.toast("⚠️ オッズ数値なし。馬番のみ読み込みます。", icon="ℹ️")
                    return res, "NaN Warning"
                    
                return res, "Success"
            
            return None, debug_logs
            
        except Exception as e:
            return None, str(e)

    # --- メイン処理 ---
    target_url = url
    if not force_mode:
        if "sp.netkeiba.com" in target_url:
            target_url = target_url.replace("sp.netkeiba.com", "race.netkeiba.com")
        if "race.netkeiba.com" in target_url and "shutuba.html" in target_url:
            target_url = target_url.replace("shutuba.html", "odds.html")
    
    result_df, msg = try_fetch(target_url)
    
    if result_df is None or result_df.empty:
        if target_url != url:
            fallback_url = url
            if "sp.netkeiba.com" in fallback_url:
                fallback_url = fallback_url.replace("sp.netkeiba.com", "race.netkeiba.com")
            
            result_df, msg_fallback = try_fetch(fallback_url)
            
            if result_df is None:
                st.error("❌ 取得失敗")
                with st.expander("🔍 詳細ログ"):
                    st.write(f"1: {msg}")
                    st.write(f"2: {msg_fallback}")
                return None, "Failed"
        else:
            st.error("❌ 取得失敗")
            with st.expander("🔍 詳細ログ"):
                st.write(msg)
            return None, "Failed"
            
    return result_df, "Success"

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
            mime="text/csv"
        )
    else:
        st.button("💾 現在の状態を保存", disabled=True)

if uploaded_file:
    df_raw, status = load_data(uploaded_file)
    df_prev, _ = load_data(prev_file) if prev_file else (None, None)
    
    if status != "success":
        st.error(status)
    else:
        if 'analyzed_df' not in st.session_state:
            if 'パターン' in df_raw.columns and 'スコア' in df_raw.columns:
                st.success("📂 保存データを検知しました。復元します。")
                result_df = df_raw
            else:
                with st.spinner('全レース分析中...'):
                    result_df = analyze_logic(df_raw, df_prev)
                    result_df = apply_ranking_logic(result_df)

            if not result_df.empty:
                result_df['id'] = result_df.index
                st.session_state['analyzed_df'] = result_df
            else:
                st.session_state['analyzed_df'] = pd.DataFrame()

        if not st.session_state['analyzed_df'].empty:
            
            st.subheader("📝 結果入力 & 推奨馬リスト")
            
            full_df = st.session_state['analyzed_df'].copy()
            places = sorted(full_df['場名'].unique())
            display_cols = ['場名', 'R', '正番', '馬名', '単ｵｯｽﾞ', '属性', 'タイプ', 'パターン', '条件', 'スコア', '着順']
            
            with st.form("result_entry_form"):
                place_tabs = st.tabs(places)
                edited_dfs = [] 
                
                for p_tab, place in zip(place_tabs, places):
                    with p_tab:
                        place_df = full_df[full_df['場名'] == place]
                        race_list = sorted(place_df['R'].unique())
                        if race_list:
                            r_tabs = st.tabs([f"{r}R" for r in race_list])
                            for r_tab, r_num in zip(r_tabs, race_list):
                                with r_tab:
                                    # --- オッズ取得機能 (デバッグ付き) ---
                                    with st.expander(f"🌐 {place}{r_num}R の最新オッズをWebから取得 (netkeiba)"):
                                        st.caption("出馬表や結果ページのURLを貼り付けてください")
                                        
                                        col_url, col_force = st.columns([3, 1])
                                        with col_url:
                                            url_input = st.text_input("URL", key=f"url_{place}_{r_num}")
                                        with col_force:
                                            force_mode = st.checkbox("URL自動変換しない\n(過去/確定レース用)", key=f"force_{place}_{r_num}")
                                        
                                        if st.form_submit_button(f"📥 {place}{r_num}R オッズ取得・更新"):
                                            if url_input:
                                                new_odds_df, debug_msg = fetch_odds_from_web(url_input, force_mode)
                                                if new_odds_df is not None:
                                                    target_mask = (st.session_state['analyzed_df']['場名'] == place) & \
                                                                  (st.session_state['analyzed_df']['R'] == r_num)
                                                    
                                                    for _, o_row in new_odds_df.iterrows():
                                                        umaban = o_row['正番']
                                                        odds = o_row['単ｵｯｽﾞ']
                                                        mask = target_mask & (st.session_state['analyzed_df']['正番'] == umaban)
                                                        st.session_state['analyzed_df'].loc[mask, '単ｵｯｽﾞ'] = odds
                                                    
                                                    st.success(f"{place}{r_num}R のオッズを更新しました！")
                                                    st.rerun()
                                                else:
                                                    pass # エラーメッセージはfetch関数内で表示済み
                                                
                                    race_data = place_df[place_df['R'] == r_num][valid_cols := [c for c in display_cols if c in full_df.columns]]
                                    edited_chunk = st.data_editor(
                                        race_data,
                                        column_config={
                                            "着順": st.column_config.NumberColumn("着順", format="%d", min_value=1, max_value=18),
                                            "スコア": st.column_config.ProgressColumn("注目度", format="%.1f", min_value=0, max_value=20),
                                            "単ｵｯｽﾞ": st.column_config.NumberColumn("オッズ", format="%.1f")
                                        },
                                        disabled=["場名", "R", "馬名", "単ｵｯｽﾞ", "正番", "属性", "タイプ", "パターン", "条件", "スコア"],
                                        hide_index=True,
                                        use_container_width=True,
                                        height=300,
                                        key=f"editor_{place}_{r_num}"
                                    )
                                    edited_dfs.append(edited_chunk)
                
                st.markdown("---")
                submit_btn = st.form_submit_button("🔄 全レースの入力を確定して更新 (再計算)")

            if submit_btn:
                if edited_dfs:
                    combined_df = pd.concat(edited_dfs, ignore_index=True)
                    recalculated_df = apply_ranking_logic(combined_df)
                    recalculated_df = recalculated_df.sort_values(['場名', 'R', '総合スコア'], ascending=[True, True, False])
                    st.session_state['analyzed_df'] = recalculated_df
                    st.success("データを更新し、スコアと推奨度を再計算しました！")
                    st.rerun()

            # ==========================================
            # 5. 集計 & グラフ
            # ==========================================
            current_df = st.session_state['analyzed_df']
            df_hits = current_df[current_df['着順'].notna()].copy()
            df_hits['着順'] = pd.to_numeric(df_hits['着順'], errors='coerce')
            df_fuku = df_hits[df_hits['着順'] <= 3] 

            st.divider()
            st.subheader("📊 リアルタイム傾向分析")

            if not df_hits.empty:
                c1, c2, c3 = st.columns(3)
                with c1: st.metric("消化レース", len(df_hits['R'].unique()))
                with c2: 
                    rate = len(df_fuku)/len(df_hits)*100 if len(df_hits)>0 else 0
                    st.metric("推奨馬 複勝率", f"{rate:.1f}%")
                with c3: st.metric("的中数", f"{len(df_fuku)} 頭")

                graph_places = sorted(df_hits['場名'].unique())
                if graph_places:
                    g_tabs = st.tabs(graph_places)
                    for g_tab, place in zip(g_tabs, graph_places):
                        with g_tab:
                            col_g1, col_g2 = st.columns([1, 1])
                            place_hits = df_hits[df_hits['場名'] == place]
                            place_fuku = df_fuku[df_fuku['場名'] == place]
                            
                            if not place_fuku.empty:
                                all_patterns = []
                                for p in place_fuku['パターン']:
                                    if p: all_patterns.extend(str(p).split(','))
                                
                                if all_patterns:
                                    pat_counts = pd.Series(all_patterns).value_counts().reset_index()
                                    pat_counts.columns = ['パターン', '的中数']
                                    with col_g1:
                                        fig = px.pie(pat_counts, values='的中数', names='パターン', 
                                                     title=f'【{place}】 的中パターン', hole=0.4)
                                        st.plotly_chart(fig, use_container_width=True)
                                else:
                                    with col_g1: st.info("パターンデータなし")
                            else:
                                with col_g1: st.info("的中データはありません")
                            
                            with col_g2:
                                st.write(f"**{place} の結果一覧**")
                                place_hits_disp = place_hits.copy()
                                place_hits_disp['馬名'] = place_hits_disp.apply(
                                    lambda x: f":blue[**{x['馬名']}**]" if '青' in str(x['パターン']) else x['馬名'], 
                                    axis=1
                                )
                                st.dataframe(place_hits_disp[['R', '馬名', '単ｵｯｽﾞ', '属性', 'タイプ', '着順']], use_container_width=True, hide_index=True)

                # --- 傾向スコア加算 & 次レース表示 & 買い目 ---
                st.markdown("### 📈 次レースの注目馬・推奨買い目")
                
                future_races = current_df[current_df['着順'].isna()].copy()
                
                if not future_races.empty:
                    future_places = sorted(future_races['場名'].unique())
                    if future_places:
                        f_tabs = st.tabs(future_places)
                        
                        for tab, place in zip(f_tabs, future_places):
                            with tab:
                                place_future = future_races[future_races['場名'] == place]
                                if not place_future.empty:
                                    future_r_list = sorted(place_future['R'].unique())
                                    r_tabs = st.tabs([f"{r}R" for r in future_r_list])
                                    
                                    for r_tab, r_num in zip(r_tabs, future_r_list):
                                        with r_tab:
                                            target_df = place_future[place_future['R'] == r_num]
                                            target_df = target_df.sort_values('総合スコア', ascending=False)
                                            
                                            target_df['馬名'] = target_df.apply(
                                                lambda x: f":blue[**{x['馬名']}**]" if '青' in str(x['パターン']) else x['馬名'], 
                                                axis=1
                                            )
                                            
                                            top_horses = target_df.head(3)
                                            if len(top_horses) >= 2:
                                                h1 = top_horses.iloc[0]
                                                h2 = top_horses.iloc[1]
                                                h1_score = h1['総合スコア']
                                                h2_score = h2['総合スコア']
                                                h1_name = str(h1['馬名']).replace(':blue[**', '').replace('**]', '')
                                                
                                                h1_odds = h1.get('単ｵｯｽﾞ', np.nan)
                                                odds_str = f"(単{h1_odds}倍)" if pd.notna(h1_odds) else "(オッズ不明)"
                                                
                                                if h1_score >= 15:
                                                    if pd.notna(h1_odds):
                                                        if h1_odds >= 3.0:
                                                            st.success(f"🔥 **{r_num}R 激アツ勝負 (高期待値)**: {h1['正番']} ({h1_name}) {odds_str}")
                                                        elif h1_odds < 1.5:
                                                            st.warning(f"🧱 **{r_num}R 鉄板 (堅実)**: {h1['正番']} ({h1_name}) {odds_str}")
                                                        else:
                                                            st.info(f"👑 **{r_num}R 盤石の軸**: {h1['正番']} ({h1_name}) {odds_str}")
                                                    else:
                                                        st.info(f"👑 **{r_num}R 盤石の軸**: {h1['正番']} ({h1_name})")
                                                elif h1_score >= 12:
                                                    st.info(f"💡 **{r_num}R 単複推奨**: {h1['正番']} ({h1_name})")
                                                else:
                                                    st.caption(f"🎲 {r_num}R は混戦模様です。")
                                            
                                            disp_cols = ['R', '馬名', '単ｵｯｽﾞ', 'タイプ', 'パターン', 'スコア', '傾向加点', '総合スコア', '推奨買い目']
                                            final_disp_cols = [c for c in disp_cols if c in target_df.columns]
                                            
                                            st.dataframe(
                                                target_df[final_disp_cols],
                                                use_container_width=True,
                                                hide_index=True
                                            )
                                else:
                                    st.info("残りレースはありません")
                    else:
                        st.info("全てのレースが終了しました。")
                else:
                    st.info("全てのレースが終了しました。")
            else:
                st.info("まだ着順が入力されていません。結果を入力して更新ボタンを押してください。")

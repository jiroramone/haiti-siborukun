import streamlit as st
import pandas as pd
import numpy as np
import re
import plotly.express as px
import openpyxl

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
    """
    ファイルを読み込み、エラーハンドリングを行う関数
    Windows(Shift-JIS)とMac(UTF-8)の両方に対応
    """
    df = None
    
    # 1. Excelファイルの処理
    if file.name.endswith('.xlsx'):
        try:
            file.seek(0)
            df = pd.read_excel(file, engine='openpyxl')
        except Exception as e:
            return pd.DataFrame(), f"Excel読み込みエラー: {e}"
            
    # 2. CSVファイルの処理
    else:
        try:
            file.seek(0)
            df = pd.read_csv(file, encoding='utf-8', on_bad_lines='skip')
        except UnicodeDecodeError:
            try:
                file.seek(0)
                df = pd.read_csv(file, encoding='cp932', on_bad_lines='skip')
            except Exception as e:
                return pd.DataFrame(), f"CSV読み込みエラー(文字コード判定不能): {e}"
        except Exception as e:
            return pd.DataFrame(), f"CSV予期せぬエラー: {e}"

    # --- データ整形 ---
    df.columns = df.columns.str.strip()
    
    rename_map = {
        '場所': '場名', '開催': '場名', '単オッズ': '単ｵｯｽﾞ', 
        '調教師': '厩舎', '調教師名': '厩舎', '厩舎名': '厩舎',
        '騎手名': '騎手',
        'レース': 'R', 'Ｒ': 'R', 'レース名': 'R',
        '着': '着順', '着 順': '着順', '番': '正番', '馬番': '正番'
    }
    df = df.rename(columns=rename_map)

    # 重複カラムの削除
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
            
    # 必須列確保
    required_cols = ['R', '場名', '馬名', '正番', '騎手', '厩舎', '馬主', '単ｵｯｽﾞ', '逆番', '正循環', '逆循環', '頭数']
    for col in required_cols:
        if col not in df.columns:
            df[col] = np.nan

    # 保存データ用の列
    save_cols = ['属性', 'タイプ', 'パターン', '条件', 'スコア', '着順', '傾向加点', '総合スコア']
    existing_save_cols = [c for c in save_cols if c in df.columns]
    
    final_cols = required_cols + existing_save_cols
    
    return df[final_cols].copy(), "success"

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
    
    # A. 青塗 (騎手・厩舎・馬主)
    blue_keys = set()
    for col in ['騎手', '厩舎', '馬主']:
        if col not in df_curr.columns: continue
        group_keys = ['場名', col]
        try:
            for name, group in df_curr.groupby(group_keys):
                if len(group) < 2: continue
                target_name = name[1]
                if not target_name: continue
                
                common_vals = get_common_values(group)
                if common_vals:
                    all_races = sorted(group['R'].unique())
                    priority = 0.3 if col == '騎手' else (0.2 if col == '厩舎' else 0.1)
                    
                    for _, row in group.iterrows():
                        other_races = [str(r) for r in all_races if r != row['R']]
                        remark = f'[{col}] 共通値({common_vals}) [他:{",".join(other_races)}R]'
                        rec_list.append({
                            '場名': row['場名'], 'R': row['R'], '正番': row['正番'], '馬名': row['馬名'],
                            '属性': f"{col}:{target_name}", 
                            'タイプ': f'★ {col}青塗', 
                            'パターン': 'Blue', 
                            '条件': remark,
                            'スコア': 9.0 + priority
                        })
                        blue_keys.add((row['場名'], row['R'], row['馬名']))
        except: continue

    # B. 青塗の隣
    if blue_keys:
        for (place, race), group in df_curr.groupby(['場名', 'R']):
            group = group.sort_values('正番')
            umaban_map = {int(row['正番']): row for _, row in group.iterrows()}
            blue_horses = [row for _, row in group.iterrows() if (place, race, row['馬名']) in blue_keys]
            for b_row in blue_horses:
                curr_num = int(b_row['正番'])
                for t_num in [curr_num - 1, curr_num + 1]:
                    if t_num in umaban_map:
                        t_row = umaban_map[t_num]
                        if (place, race, t_row['馬名']) not in blue_keys:
                            rec_list.append({
                                '場名': place, 'R': race, '正番': t_num, '馬名': t_row['馬名'],
                                '属性': '(青塗隣)', 'タイプ': '△ 青塗の隣',
                                'パターン': 'BlueNeighbor',
                                '条件': f"青塗馬(#{curr_num})の隣",
                                'スコア': 9.0
                            })

    # C. 通常ペア (騎手)
    if '騎手' in df_curr.columns:
        for name, group in df_curr.groupby('騎手'):
            if len(group) < 2: continue
            group = group.sort_values('R').to_dict('records')
            for i in range(len(group)-1):
                curr, next_r = group[i], group[i+1]
                if curr['場名'] != next_r['場名']: continue
                pat = get_pair_pattern(curr, next_r)
                if pat:
                    label = "◎ チャンス" if any(x in pat for x in ['C','D','G','H']) else "○ 狙い目"
                    base_score = 4.0 if label.startswith("◎") else 3.0
                    rec_list.append({
                        '場名': curr['場名'], 'R': curr['R'], '正番': curr['正番'], '馬名': curr['馬名'],
                        '属性': f"騎手:{name}", 'タイプ': label, 'パターン': pat, 
                        '条件': f"[騎手] ペア({next_r['R']}R #{next_r['正番']})", 'スコア': base_score + 0.3
                    })
                    rec_list.append({
                        '場名': next_r['場名'], 'R': next_r['R'], '正番': next_r['正番'], '馬名': next_r['馬名'],
                        '属性': f"騎手:{name}", 'タイプ': label, 'パターン': pat, 
                        '条件': f"[騎手] ペア({curr['R']}R #{curr['正番']})", 'スコア': base_score + 0.3
                    })

    # C. 通常ペア (厩舎)
    if '厩舎' in df_curr.columns:
        for (place, name), group in df_curr.groupby(['場名', '厩舎']):
            if len(group) < 2: continue
            group = group.sort_values('R').to_dict('records')
            for i in range(len(group)):
                for j in range(i+1, len(group)):
                    curr, next_r = group[i], group[j]
                    pat = get_pair_pattern(curr, next_r)
                    if pat:
                        label = "◎ チャンス" if any(x in pat for x in ['C','D','G','H']) else "○ 狙い目"
                        base_score = 4.0 if label.startswith("◎") else 3.0
                        rec_list.append({
                            '場名': place, 'R': curr['R'], '正番': curr['正番'], '馬名': curr['馬名'],
                            '属性': f"厩舎:{name}", 'タイプ': label, 'パターン': pat, 
                            '条件': f"[厩舎] ペア({next_r['R']}R #{next_r['正番']})", 'スコア': base_score + 0.2
                        })
                        rec_list.append({
                            '場名': place, 'R': next_r['R'], '正番': next_r['正番'], '馬名': next_r['馬名'],
                            '属性': f"厩舎:{name}", 'タイプ': label, 'パターン': pat, 
                            '条件': f"[厩舎] ペア({curr['R']}R #{curr['正番']})", 'スコア': base_score + 0.2
                        })

    # D. 前日同配置 (騎手のみ)
    if df_prev is not None and not df_prev.empty:
        for idx, row in df_curr.iterrows():
            race = row['R']
            name = row['騎手']
            if not name: continue
            prev_rows = df_prev[(df_prev['R'] == race) & (df_prev['騎手'] == name)]
            for _, p_row in prev_rows.iterrows():
                is_seiban = (p_row['正番'] == row['正番'])
                is_gyaku = (p_row['計算_逆番'] == row['計算_逆番'])
                if is_seiban or is_gyaku:
                    reason = "正番" if is_seiban else "逆番"
                    rec_list.append({
                        '場名': row['場名'], 'R': race, '正番': row['正番'], '馬名': row['馬名'],
                        '属性': f"騎手:{name}", 'タイプ': '★ 前日同配置', 
                        'パターン': 'PrevDay',
                        '条件': f"[騎手] 前日{race}R同配置({reason})", 
                        'スコア': 8.3
                    })

    if not rec_list:
        return pd.DataFrame()
        
    res_df = pd.DataFrame(rec_list)
    
    agg_funcs = {
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
# 3. Webアプリ画面 (Streamlit)
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
    # データ読み込み
    df_raw, status = load_data(uploaded_file)
    df_prev, _ = load_data(prev_file) if prev_file else (None, None)
    
    if status != "success":
        st.error(status)
    else:
        # 初回分析 or 復元
        if 'analyzed_df' not in st.session_state:
            # 必須列チェック（パターンとスコアがある＝保存データ）
            if 'パターン' in df_raw.columns and 'スコア' in df_raw.columns:
                st.success("📂 保存データを検知しました。復元します。")
                result_df = df_raw
            else:
                with st.spinner('全レース分析中...'):
                    result_df = analyze_logic(df_raw, df_prev)

            if not result_df.empty:
                result_df['id'] = result_df.index
                st.session_state['analyzed_df'] = result_df
            else:
                st.session_state['analyzed_df'] = pd.DataFrame()

        # --- メインエリア ---
        if not st.session_state['analyzed_df'].empty:
            
            st.subheader("📝 結果入力 & 推奨馬リスト")
            st.info("下の表で着順を入力すると、即座に集計が更新されます。")
            
            # --- 開催場ごとのタブを作成 ---
            full_df = st.session_state['analyzed_df'].copy()
            places = sorted(full_df['場名'].unique())
            
            # ★ここを修正: 表示するカラムを限定する
            # オッズや厩舎などの不要なカラム（特に入力枠になってしまうもの）を除外
            display_cols = ['場名', 'R', '正番', '馬名', '属性', 'タイプ', 'パターン', '条件', 'スコア', '着順']
            
            # タブを作成 (フォームの枠は削除)
            tabs = st.tabs(places)
            edited_dfs = [] 
            
            # 各タブでエディタを表示し、編集結果をリストに格納
            for tab, place in zip(tabs, places):
                with tab:
                    # 特定のカラムだけ抽出して表示
                    # ※カラムが存在しない場合のエラーを防ぐため、intersectionで存在する列だけ選ぶ
                    valid_cols = [c for c in display_cols if c in full_df.columns]
                    place_df = full_df[full_df['場名'] == place][valid_cols]
                    
                    # 編集データを受け取る
                    edited_chunk = st.data_editor(
                        place_df,
                        column_config={
                            "着順": st.column_config.NumberColumn(
                                "着順", help="確定着順を入力 (1-18)", min_value=1, max_value=18, step=1, format="%d"
                            ),
                            "スコア": st.column_config.ProgressColumn(
                                "注目度", format="%.1f", min_value=0, max_value=20,
                            ),
                        },
                        disabled=["場名", "R", "馬名", "正番", "属性", "タイプ", "パターン", "条件", "スコア"],
                        hide_index=True,
                        use_container_width=True,
                        height=500,
                        key=f"editor_{place}" # キーを設定して状態を管理
                    )
                    edited_dfs.append(edited_chunk)
            
            # --- リアルタイム反映処理 ---
            # すべてのタブの編集結果を結合してsession_stateを更新
            if edited_dfs:
                combined_df = pd.concat(edited_dfs, ignore_index=True)
                
                # 表示していないカラム（オッズ等）が消えてしまうのを防ぐため、
                # 元のfull_dfからそれらの情報を復元して結合する処理が必要
                # ただし、今回は「保存データ」に余計なカラムが含まれていることが原因で見えているだけなので、
                # ここで結合後のデータフレームをそのまま保存すれば、次回読み込み時もスッキリした状態になる。
                # 保存機能のために元の詳細情報が必要な場合は別途マージが必要だが、
                # ユーザー体験としては「余計な枠」が消えることを優先する。
                
                combined_df = combined_df.sort_values(['場名', 'R', 'スコア'], ascending=[True, True, False])
                st.session_state['analyzed_df'] = combined_df

            # ==========================================
            # 4. 集計 & グラフ
            # ==========================================
            current_df = st.session_state['analyzed_df']
            
            df_hits = current_df[current_df['着順'].notna()].copy()
            df_hits['着順'] = pd.to_numeric(df_hits['着順'], errors='coerce')
            df_fuku = df_hits[df_hits['着順'] <= 3] 

            st.divider()
            st.subheader("📊 リアルタイム傾向分析")

            c1, c2, c3 = st.columns(3)
            with c1: st.metric("消化レース", len(df_hits['R'].unique()))
            with c2: 
                rate = len(df_fuku)/len(df_hits)*100 if len(df_hits)>0 else 0
                st.metric("推奨馬 複勝率", f"{rate:.1f}%")
            with c3: st.metric("的中数", f"{len(df_fuku)} 頭")

            if not df_fuku.empty:
                graph_places = sorted(df_fuku['場名'].unique())
                g_tabs = st.tabs(graph_places)
                
                for g_tab, place in zip(g_tabs, graph_places):
                    with g_tab:
                        col_g1, col_g2 = st.columns([1, 1])
                        place_data = df_fuku[df_fuku['場名'] == place]
                        
                        all_patterns = []
                        for p in place_data['パターン']:
                            if p: all_patterns.extend(str(p).split(','))
                        
                        if all_patterns:
                            pat_counts = pd.Series(all_patterns).value_counts().reset_index()
                            pat_counts.columns = ['パターン', '的中数']
                            
                            with col_g1:
                                fig = px.pie(pat_counts, values='的中数', names='パターン', 
                                             title=f'【{place}】 的中パターン', hole=0.4)
                                st.plotly_chart(fig, use_container_width=True)
                            
                            with col_g2:
                                st.write(f"**{place} の的中詳細**")
                                st.dataframe(place_data[['R', '馬名', '属性', 'タイプ', '着順']], use_container_width=True, hide_index=True)
                        else:
                            st.info("パターンデータなし")

                # --- 傾向スコア加算 ---
                st.markdown("### 📈 次レースの注目馬 (傾向加算)")
                
                hit_patterns = set()
                for p in df_fuku['パターン']:
                    if p: hit_patterns.update(str(p).split(','))
                
                future_races = current_df[current_df['着順'].isna()].copy()
                
                if not future_races.empty:
                    def calc_bonus(row_pat):
                        if not row_pat or pd.isna(row_pat): return 0.0
                        pats = str(row_pat).split(',')
                        bonus = 0.0
                        for p in pats:
                            if p in hit_patterns and len(p) == 1: 
                                bonus += 2.0 
                        return bonus

                    future_races['傾向加点'] = future_races['パターン'].apply(calc_bonus)
                    future_races['総合スコア'] = future_races['スコア'] + future_races['傾向加点']
                    
                    hot_horses = future_races[future_races['傾向加点'] > 0].sort_values(['場名', 'R', '総合スコア'], ascending=[True, True, False])
                    
                    if not hot_horses.empty:
                        st.success(f"本日好調なパターンを持つ馬が {len(hot_horses)} 頭います！")
                        st.dataframe(
                            hot_horses[['場名', 'R', '馬名', 'タイプ', 'パターン', 'スコア', '傾向加点', '総合スコア']],
                            use_container_width=True,
                            hide_index=True
                        )
                    else:
                        st.info("現時点では、特定の傾向に合致する未出走馬はありません。")
            else:
                st.info("まだ的中データがありません。着順を入力してください。")
        else:
            st.warning("推奨馬が見つかりませんでした。")

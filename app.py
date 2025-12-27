# ==========================================
# 修正版: 共通値の抽出ロジック (全頭一致ではなく、重複検出に変更)
# ==========================================
from collections import Counter

def analyze_logic(df_curr, df_prev=None):
    df_curr = calc_haichi_numbers(df_curr)
    if df_prev is not None and not df_prev.empty:
        df_prev = calc_haichi_numbers(df_prev)
    
    rec_list = []
    
    # -------------------------------------------------------
    # A. 青塗 (ロジック修正版)
    # -------------------------------------------------------
    blue_keys = set()
    
    # 騎手、厩舎、馬主すべて「場名」を含めてグルーピングする
    for col in ['騎手', '厩舎', '馬主']:
        if col not in df_curr.columns: continue
        
        # 【修正1】場名を含めてグループ化しないと、別会場のデータと混ざって逆番がズレる
        group_keys = ['場名', col]
        
        try:
            for name_key, group in df_curr.groupby(group_keys):
                if len(group) < 2: continue
                
                # name_keyは (場名, 名前) のタプルになる
                place_name = name_key[0]
                target_name = name_key[1]
                if not target_name: continue

                # --- 【修正2】 グループ内の「数字の出現頻度」を調べる ---
                # 各馬が持っている数字（正・逆・循環）をすべてリストアップする
                all_numbers = []
                # 各馬がどの数字を持っているかを記録するマップ {index: {num1, num2...}}
                horse_numbers_map = {}

                target_cols = ['正番', '計算_逆番', '計算_正循環', '計算_逆循環']

                for idx, row in group.iterrows():
                    my_nums = set()
                    for c in target_cols:
                        val = row.get(c)
                        if pd.notna(val):
                            try:
                                n = int(float(val))
                                if n != 0: my_nums.add(n)
                            except:
                                continue
                    
                    horse_numbers_map[idx] = my_nums
                    all_numbers.extend(list(my_nums))

                # 出現回数が2回以上の数字（共通数字）を特定
                counts = Counter(all_numbers)
                common_vals = {num for num, cnt in counts.items() if cnt >= 2}

                # 共通数字を持っている馬だけを抽出してレコード追加
                if common_vals:
                    priority = 1.0 if col == '騎手' else 0.2
                    
                    for idx, row in group.iterrows():
                        my_nums = horse_numbers_map.get(idx, set())
                        # この馬が持っている数字の中に、共通数字が含まれているか？
                        matched_nums = my_nums.intersection(common_vals)
                        
                        if matched_nums:
                            matched_str = ','.join(map(str, sorted(matched_nums)))
                            
                            # 自分以外の同属性馬（同じ共通値を持つ馬）を探して表示用に整形
                            others = []
                            for o_idx, o_row in group.iterrows():
                                if idx == o_idx: continue
                                o_nums = horse_numbers_map.get(o_idx, set())
                                # 相手も同じ共通数字を持っていればペア
                                if not o_nums.isdisjoint(matched_nums):
                                    others.append(f"{o_row['R']}R")
                            
                            other_races_str = ",".join(sorted(list(set(others))))
                            remark = f'[{col}] 共通({matched_str}) [他:{other_races_str}]'
                            
                            rec_list.append({
                                '場名': row['場名'], 'R': row['R'], '正番': row['正番'], '馬名': row['馬名'],
                                '単ｵｯｽﾞ': row.get('単ｵｯｽﾞ', np.nan),
                                '属性': f"{col}:{target_name}", 
                                'タイプ': f'★ {col}青塗', 
                                'パターン': '青', 
                                '条件': remark,
                                'スコア': 9.0 + priority
                            })
                            blue_keys.add((row['場名'], row['R'], row['馬名'], f"{col}:{target_name}"))

        except Exception as e:
            # エラー時はスキップして続行（デバッグ用にst.write(e)しても良い）
            continue

    # -------------------------------------------------------
    # B. 青塗の隣 (既存ロジック継続)
    # -------------------------------------------------------
    if blue_keys:
        # (以下、既存コードと同じため省略可、変更なし)
        blue_lookup = {}
        for b in blue_keys:
            key = (b[0], b[1]) 
            if key not in blue_lookup: blue_lookup[key] = []
            blue_lookup[key].append({'馬名': b[2], '属性': b[3]})

        for (place, race), group in df_curr.groupby(['場名', 'R']):
            # ... (中略：既存の「青塗の隣」ロジックをそのまま貼り付け) ...
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

    # ... (以下、C.通常ペア、D.前日同配置などは既存のまま継続) ...
    
    # C. 通常ペア (騎手)
    # (元のコードをここに続けてください)
    if '騎手' in df_curr.columns:
         for (place, name), group in df_curr.groupby(['場名', '騎手']):
            # ... (省略) ...
            pass # ここに元のロジックが入ります

    # ... (analyze_logicの残りの部分) ...

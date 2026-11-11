# FC27 字幕中译 · 术语锁定表与人名规则（译前词表 / term injection）

> 本文件是翻译 AI 的**前置词表**，必须先于 llm_prompt.txt（20 条指令）注入。
> 来源：《FC27 字幕中译规范（合并版 · 2026-11-11）》第 4、5 节。
> 球员、教练、俱乐部一律保留英文标准拼写，本表不产出中文译名。
> 完整球员名单见仓库 `player_names_en.txt`（19713 人，供人工/离线复核）；本词表只列高频 ASR 误听形态。

## A. 四桶归位（遇到可疑词先归桶，顺序不许换）

1. **人名桶**：紧跟 card / promo / icon / hero / evo / SBC / 评分数字；前有 it's / that's / we got / another；开包瞬间单独成句；"不是 X，是 Y"里被否定的一方；"某国籍/俱乐部/位置 + 词"。→ 写标准英文拼写或标（待核），禁止按字面译成食物、器官、天气、动作、职位。
2. **位置桶**：CB / CM / CDM / CAM / LB / RB / ST / LW / RW / LM / RM / box-to-box，且同段多个球员共用。→ 按 B 节位置表写唯一中文。
3. **术语桶**：wild card / champions / chemistry / fodder / walkout / promo / pack / evo / PlayStyle。→ 按 B 节术语表写唯一中文，禁止中文修饰 + 英文名词的半译。
4. **噪声桶**：语法不通、无意义词串、明显 ASR 拆词。→ 还原专名 /（听不清）/ 按术语表；禁止把英文碎片留在中文正文里当句子成分。

## B. 术语锁定表（唯一写法，全篇不许换）

### B1 活动 / 卡包 / 机制

| 英文 | 唯一写法 | 禁止 |
| --- | --- | --- |
| Road to the Knockout / RTTK / rotor knockout | RTTK（晋级淘汰赛） | 周黑、旋转大保健、旋风淘汰、锁包卡、通往淘汰赛的路、全息淘汰赛卡 |
| FUT Champions / footchamp / foot champs | FUT冠军 | 周赛、周赛冠军、周黑 |
| Team of the Week / TOTW | 周最佳阵容（卡） | 周黑 |
| Ultimate Scream | 终极尖叫 | 尖叫活动 |
| Ultimate Scream Token | 尖叫代币 | 尖叫币 |
| FUT Gallery / Gallery Token | FUT收藏馆 / 收藏馆代币 | — |
| FC Heritage / Circuit | FC传承 / FC传承巡回 | fc传承 Circuit |
| Live Events | 实时赛事 | — |
| Conference League | 欧协联 | 会议 |
| Women's Champions League | 女足欧冠 | 女子冠军赛、女冠军Z卡 |
| Europa League | 欧联 | 欧罗巴联赛（须全篇统一） |
| Champions（卡种前缀） | 冠军赛 | 冠军卡 |
| Hero / Icon | 英雄卡 / 传奇卡 | 英雄、传奇球星（混用） |
| promo | 活动（卡） | 促销 |
| Winter Wildcard | 冬野卡 | 冬日 wild card |
| player pick (1 of 5) | 5 选 1 球员挑选 | 一包或者五包、一或五星 |
| 88+×3 / 83+×5 / 84+×4 / 86+×5 | 88+×3 包 / 83+×5 包 … | 88 免费、83 个五包、A5×3、88+三黄卡 |
| 88 free | 88 免费包 | 88+免费包（多写加号） |
| 85 plus times free | 85+ 免费包 | 85 分的自由球 |
| 100k pack | 10 万金币包 | 100k 包（可保留，须统一） |
| pack（动词） | 开出 / 开到 | 打包 |
| open | 开 | — |
| pull | 抽到 | — |
| walkout / walking out | 爆卡出场（简写"爆卡"） | 走出球场、走出动画、爆包、走大卡、全息卡 |
| holographic / holo | 全息（卡/版） | 空壳、空壳版 |
| quick sell | 快速出售 / 秒卖 | 快速设置 |
| SBC | SBC（保留缩写） | 组队挑战（可作解释，不作主译） |
| fodder | 狗粮 | — |
| dupe | 重复卡 | 双卡 |
| chemistry / full chemistry | 化学 / 化学拉满 | 回家化学加成、玩两个 |
| Chemistry Style | 化学风格 | 化学加成（那是 chemistry） |
| evolution / evo | 进化 | — |
| PlayStyle / PlayStyle+ | 比赛风格 / 比赛风格+ | 玩法加成、风格加成、操作损失值、丢球、技能 |
| Low Driven (Shot) | 低射 | 大力低射 |
| Power Shot | 大力射门 | — |
| Precision Header | 精准头球 | — |
| Pinged Pass | Pinged Pass（精准长传） | 被打扰、pinged base |
| Incisive Pass | Incisive Pass（穿透传球） | 锋利 |
| Acrobatic | Acrobatic（凌空） | 杂耍技能 |
| Intercept / Anticipate / Block / Jockey | 拦截 / 预判 / 封堵 / 跟防 | — |
| Chip Shot | 吊射 | — |
| Rush（任务条件用语） | 在Rush中通过完成奖励目标获取 | 在 Rush 中完成加成 |
| rank 1（段位/排名） | rank 1（保留英文） | 顶级、第一名、排名第一 |
| elite（段位） | 精英 | 顶级、高级 |

### B2 位置（只选一套，全篇不换）

| 英文 | 唯一写法 | 禁止 |
| --- | --- | --- |
| CB / centre back / sender back / send the back | 中后卫（CB） | 后腰、后场发送者、工具人、送回去 |
| CM / centre mid / sentiment | 中场（CM） | 情绪、心情、中前卫（混用） |
| CDM | 后腰（CDM） | 中后卫 |
| CAM | 前腰（CAM） | 中场 |
| LB / RB | 左/右后卫 | — |
| LM / RM | 左/右中场 | 与边锋混用 |
| LW / RW | 左/右边锋 | 与边中场混用 |
| ST / CF | 中锋 / 影锋 | — |
| box-to-box / boxer box | B2B 中场 / 全能中场 | 拳击手 |

> winger / swinger = 边锋（位置词，不是人名）。English = 英格兰（国籍），tall = 高个（身高）；国籍、身高、联赛是三个独立字段，不许因一个词听不懂就把整句改意。
> mid = 一般般 / 平庸（评价词，永远不是位置词）；middle stat = 中等档属性（不是"中路"）。

### B3 卡包名四要素（逐项对齐原句）

模板：`{评分门槛}{+或空}{×N | 免费包}`

- 门槛照抄（82/83/84/85/86/88），不并档、不四舍五入。
- **加号由门槛数字后有没有 plus 决定**，不因后面是 free 而丢。
- times three / five / ten / four → ×3 / ×5 / ×10 / ×4（是几个包，不是"选三个"）。
- free → 免费包；`×N` 与 `免费包` 是同一位上的两种后缀，只能选一个。
- `88 free` = **88 免费包**（不带 +）；`85 plus times free` = **85+ 免费包**（带 +）；`88 plus times three` = **88+×3**；`84 for four rota knockout` = **84+×4 搏 RTTK**。
- 88 free 与 88+×3 是两种不同卡包，不许互替。

### B4 数字与单位

| 原句形态 | 写法 |
| --- | --- |
| 700K / seven hundred thousand | 70 万 |
| 8 million / 8 mil | 800 万 |
| 13 K | 1.3 万 |
| 1500 fc points | 1500 FC 点 |
| 87 rated / overall 87 | 87 分（不写"87 级"） |
| over one percent | 超过 1% |
| 88+×3 里的 88 | 评分门槛，不是数量、不是价格 |

## C. 人名处理规则（不产出中文译名）

一律保留英文标准拼写，但"保留英文"不等于"照抄 ASR"。

- **N1** 按人名三步：先判是不是人名（B/A 节人名桶信号），再凑国籍 + 位置 + 俱乐部/评分两条，凑不到就保留 ASR 原拼 +（待核），绝不编一个像人名的英文。
- **N2** 确定身份后写标准英文拼写，带变音符号：Bonmatí、Ødegaard、Mbappé、Rúben Dias。
- **N3** 全文只允许一个形态；同一人出现两个名字视为严重错误。
- **N4** 禁止中文音译、中文绰号（魔笛/十一娃娃/棒棒哒）、"名+中文姓"混排、姓名倒置。
- **N5** 待核只标最小单位（那一个词），不标整句；已能确认的禁止标待核。

### C1 高频 ASR 错误形态（→ 后为正确写法，命中即还原）

| ASR 形态 | 实际是 |
| --- | --- |
| court wall / court war / call it wall | Courtois |
| rice / rice with / hit rice | Declan Rice |
| Kim itch / kim it's wrong | Kimmich |
| peanut | Pina |
| fatigue | Fati |
| ballet in | Banda |
| drones / city free drones | duds（不是人名，是烂卡） |
| sentiment / sent to me | centre mid（位置，不是人名） |
| Bon Matty / bond Matty | Bonmatí |
| Valverde | Valverde |
| Hon / honor / on a battel | Ona Batlle |
| Capdevila / cap the via / captain via | Capdevila |
| Ledley King / lelie king | Ledley King |
| Sneijder / Wesley Schneider | Sneijder |
| Kuyt / dirt cowl | Kuyt |
| Correa / cornea / Crrea | Correa |
| Salgado / salgarda | Salgado |
| Nuno Mendes / normal mench | Nuno Mendes |
| Tielemans / tenements | Tielemans |
| James Garner（倒置） | James Garner |
| Russo / Rousseau | Russo |
| Marquinhos / Márquez / Maguire（三向混淆） | 按卡面区分 |
| João Cancelo（七种写法） | João Cancelo |
| Kerkez / Kinsella（六种写法） | 按卡面区分 |
| McTominay / McSauce | McTominay |
| Stanway / stand way / Stan waste | Georgia Stanway |
| Bellingham | Bellingham |
| Lewandowski / Dollski | Lewandowski |
| Donnarumma / Naroma | Donnarumma |
| Debinha（含"丁丁"） | Debinha |
| Marmoush（八种写法） | Marmoush |
| Troy Parrott / Parrott（"鹦鹉"） | Troy Parrott |
| Modric（"魔笛"） | Modric |
| Wirtz / verts | Wirtz |
| Raphinha / Rafinha / my finger | Raphinha |
| Mariona / Patricia（混排） | 按卡面区分 |
| Hakimi | Hakimi |
| Ballon d'Or / ballon door | Ballon d'Or（金球奖） |
| Mapi Leon / mappily on / mapeleon | Mapi Leon |
| Hasegawa / has a gala | Hasegawa |
| Kachawi / Canall? / Boteas 类 | 按卡面复核，暂保留原拼 +（待核） |
| Gabrielle | Gabriel |
| Lavel / Lavel Sacka / Sacka | Saka |
| Sam car | Sam Kerr |
| Musa Caicedo | Moisés Caicedo |
| a killing and Mbappe / a killing Mbappe | Kylian Mbappé |

> 未列入的新名字，按 N1–N5：识别人名 → 还原英文 → 不确定就保留原拼 +（待核）。

"""从两个库的真实 Corpus（r1-4-corpus）原样复制的 Chunk 正文，供渲染测试使用；data/ 不进 Git，所以放在这里。"""

TOXICOLOGY_ATOM_TABLE = '数据库: toxicology; 表: "main"."atom"\n表用途: 记录分子中的原子及其元素类型，用于分析分子的元素组成或按元素筛选原子。\n记录粒度: 一个原子，关联其所属分子。\n字段目录: "atom_id", "molecule_id", "element"\n行标识线索（主键声明、成员顺序）: "atom_id"\n出向关联（声明外键）: "molecule_id" → "main"."molecule" ("molecule_id")'

TOXICOLOGY_CONNECTED_TABLE = '数据库: toxicology; 表: "main"."connected"\n表用途: 记录两个原子之间由哪条化学键连接，将原子与化学键组织成可查询的连接关系。\n记录粒度: 一对原子及连接它们的化学键。\n字段目录: "atom_id", "atom_id2", "bond_id"\n行标识线索（主键声明、成员顺序）: "atom_id", "atom_id2"\n出向关联（声明外键）: "bond_id" → "main"."bond" ("bond_id"); "atom_id2" → "main"."atom" ("atom_id"); "atom_id" → "main"."atom" ("atom_id")'

TOXICOLOGY_ATOM_ELEMENT = '数据库: toxicology; 字段: "main"."atom"."element"\n声明类型: TEXT\nnot_null 声明标志: False; 主键位置: 0; hidden: 0\n默认 SQL 表达式（仅文本）: NULL\n展示名: 来源未说明\n字段解释: the element of the toxicology\n取值解释: • cl: chlorine\n• c: carbon\n• h: hydrogen\n• o: oxygen\n• s: sulfur\n• n: nitrogen\n• p: phosphorus\n• na: sodium\n• br: bromine\n• f: fluorine\n• i: iodine\n• sn: Tin\n• pb: lead\n• te: tellurium\n• ca: Calcium'

TOXICOLOGY_ATOM_ATOM_ID = '数据库: toxicology; 字段: "main"."atom"."atom_id"\n声明类型: TEXT\nnot_null 声明标志: True; 主键位置: 1; hidden: 0\n默认 SQL 表达式（仅文本）: 未声明\n展示名: atom id\n字段解释: the unique id of atoms\n取值解释: 来源未说明'

TOXICOLOGY_CONNECTED_FK1 = '数据库: toxicology; 声明外键 "main"."connected" / 1\n引用方向: 源表 "main"."connected" → 目标表 "main"."atom"。\n同一条外键的有序字段对: "main"."connected"."atom_id2" → "main"."atom"."atom_id"\n关联语义: 连接关系中的第二个原子；可查询第二端原子的元素属性，与 atom_id 的第一端角色区分。\n候选 JOIN（source 为源表别名，target 为目标表别名；仅展示 ON 条件）: "source"."atom_id2" = "target"."atom_id"\n角色区别: 源表有 2 条外键指向 "main"."atom"，源端角色分别为:\n- 外键 1: "atom_id2"\n- 外键 2: "atom_id"\n同时引用这些角色时，为目标表使用不同别名，分别连接各自源列。\nON UPDATE CASCADE; ON DELETE CASCADE; MATCH NONE'

TOXICOLOGY_CONNECTED_FK2 = '数据库: toxicology; 声明外键 "main"."connected" / 2\n引用方向: 源表 "main"."connected" → 目标表 "main"."atom"。\n同一条外键的有序字段对: "main"."connected"."atom_id" → "main"."atom"."atom_id"\n关联语义: 连接关系中的第一个原子；可查询第一端原子的元素属性。\n候选 JOIN（source 为源表别名，target 为目标表别名；仅展示 ON 条件）: "source"."atom_id" = "target"."atom_id"\n角色区别: 源表有 2 条外键指向 "main"."atom"，源端角色分别为:\n- 外键 1: "atom_id2"\n- 外键 2: "atom_id"\n同时引用这些角色时，为目标表使用不同别名，分别连接各自源列。\nON UPDATE CASCADE; ON DELETE CASCADE; MATCH NONE'

TOXICOLOGY_ATOM_FK0 = '数据库: toxicology; 声明外键 "main"."atom" / 0\n引用方向: 源表 "main"."atom" → 目标表 "main"."molecule"。\n同一条外键的有序字段对: "main"."atom"."molecule_id" → "main"."molecule"."molecule_id"\n关联语义: 原子属于该分子；可将原子元素组成与分子的致癌性标签关联分析。\n候选 JOIN（source 为源表别名，target 为目标表别名；仅展示 ON 条件）: "source"."molecule_id" = "target"."molecule_id"\nON UPDATE NO ACTION; ON DELETE NO ACTION; MATCH NONE'

STUDENT_CLUB_MEMBER_TABLE = '数据库: student_club; 表: "main"."member"\n表用途: 记录社团会员的姓名、在社团中的职位、联系方式和 T 恤尺码，是按会员筛选、统计并关联专业、家乡和活动的入口。\n记录粒度: 一名社团会员。\n字段目录: "member_id", "first_name", "last_name", "email", "position", "t_shirt_size", "phone", "zip", "link_to_major"\n行标识线索（主键声明、成员顺序）: "member_id"\n出向关联（声明外键）: "zip" → "main"."zip_code" ("zip_code"); "link_to_major" → "main"."major" ("major_id")'

STUDENT_CLUB_ATTENDANCE_TABLE = '数据库: student_club; 表: "main"."attendance"\n表用途: 记录哪位会员参加了哪场活动，用于统计活动的出席人数或会员参加过的活动。\n记录粒度: 一名会员出席一场活动。\n字段目录: "link_to_event", "link_to_member"\n行标识线索（主键声明、成员顺序）: "link_to_event", "link_to_member"\n出向关联（声明外键）: "link_to_member" → "main"."member" ("member_id"); "link_to_event" → "main"."event" ("event_id")'

STUDENT_CLUB_EVENT_TYPE = '数据库: student_club; 字段: "main"."event"."type"\n声明类型: TEXT\nnot_null 声明标志: False; 主键位置: 0; hidden: 0\n默认 SQL 表达式（仅文本）: 未声明\n展示名: 来源未说明\n字段解释: The kind of event, such as game, social, election\n取值解释: 来源未说明'

STUDENT_CLUB_MEMBER_POSITION = '数据库: student_club; 字段: "main"."member"."position"\n声明类型: TEXT\nnot_null 声明标志: False; 主键位置: 0; hidden: 0\n默认 SQL 表达式（仅文本）: 未声明\n展示名: 来源未说明\n字段解释: The position the member holds in the club\n取值解释: 来源未说明'

STUDENT_CLUB_ATTENDANCE_FK0 = '数据库: student_club; 声明外键 "main"."attendance" / 0\n引用方向: 源表 "main"."attendance" → 目标表 "main"."member"。\n同一条外键的有序字段对: "main"."attendance"."link_to_member" → "main"."member"."member_id"\n关联语义: 出席记录对应的会员；可查询会员参加过哪些活动，或按会员属性分析出席情况。\n候选 JOIN（source 为源表别名，target 为目标表别名；仅展示 ON 条件）: "source"."link_to_member" = "target"."member_id"\nON UPDATE NO ACTION; ON DELETE NO ACTION; MATCH NONE'

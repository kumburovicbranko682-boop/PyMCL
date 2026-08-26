# -*- coding: utf-8 -*-
"""中文模组/整合包别名数据库 + 热门推荐。

用于支持中文搜索：当用户输入中文关键词时，通过此模块
映射到 Modrinth slug / CurseForge ID 等可下载来源。

数据来源：
- PCL2 社区维护的模组/整合包别名表
- 各中文 Minecraft 论坛的常用名称
- MCBBS 热门模组整理
"""

# 「机械动力整合包」默认指向 CBC 黄铜协奏曲（Forge 1.20.1）。
CBC_CF_ID = 1238396
CBC_CF_SLUG = "create-the-brass-concerto"
CDC_CF_ID = 1059094
CDC_CF_SLUG = "create-delight-remake"

# ----------------------------------------------------------------
# 模组中文别名 -> Modrinth slug / CurseForge ID / 其他来源
# 格式: { "中文名": {"slug": "modrinth-slug", "cf": 12345, "title": "英文原名"} }
# ----------------------------------------------------------------
MOD_ALIASES = {
    # ========== 常用模组 ==========
    "jei": {"slug": "jei", "title": "Just Enough Items (JEI)"},
    "jei物品管理器": {"slug": "jei", "title": "Just Enough Items (JEI)"},
    "物品管理器": {"slug": "jei", "title": "Just Enough Items (JEI)"},
    "just enough items": {"slug": "jei", "title": "Just Enough Items (JEI)"},

    "rei": {"slug": "rei", "title": "Roughly Enough Items (REI)"},
    "roughly enough items": {"slug": "rei", "title": "Roughly Enough Items (REI)"},

    "emi": {"slug": "emi", "title": "EMI"},
    "emi物品管理器": {"slug": "emi", "title": "EMI"},

    "小地图": {"slug": "xaeros-minimap", "title": "Xaero's Minimap"},
    "xaeros小地图": {"slug": "xaeros-minimap", "title": "Xaero's Minimap"},
    "xaero's minimap": {"slug": "xaeros-minimap", "title": "Xaero's Minimap"},

    "世界地图": {"slug": "xaeros-world-map", "title": "Xaero's World Map"},
    "xaeros世界地图": {"slug": "xaeros-world-map", "title": "Xaero's World Map"},

    # OptiFine 不在 Modrinth 上架：不给 slug，让全文搜索用 title 接手（不要偷换成 Iris）
    "高清修复": {"title": "OptiFine"},
    "optifine": {"title": "OptiFine"},
    "光影": {"slug": "iris", "title": "Iris Shaders"},

    "fabric api": {"slug": "fabric-api", "title": "Fabric API"},
    "fabric接口": {"slug": "fabric-api", "title": "Fabric API"},

    "群峦传说": {"slug": "terrafirmacraft", "cf": 302973, "title": "TerraFirmaCraft"},
    "terrafirmacraft": {"slug": "terrafirmacraft", "cf": 302973, "title": "TerraFirmaCraft"},
    "群峦": {"slug": "terrafirmacraft", "cf": 302973, "title": "TerraFirmaCraft"},
    "tfc": {"slug": "terrafirmacraft", "cf": 302973, "title": "TerraFirmaCraft"},
    "HBM核科技": {"cf": 235439, "title": "Hbm's Nuclear Tech Mod"},
    "hbm nuclear tech": {"cf": 235439, "title": "Hbm's Nuclear Tech Mod"},

    "旅行地图": {"slug": "journeymap", "cf": 32274, "title": "JourneyMap"},
    "journeymap": {"slug": "journeymap", "cf": 32274, "title": "JourneyMap"},
    "journey map": {"slug": "journeymap", "cf": 32274, "title": "JourneyMap"},

    "waila": {"slug": "waila", "title": "Hwyla / Waila"},
    "高亮显示": {"slug": "waila", "title": "Hwyla / Waila"},
    "看到方块信息": {"slug": "waila", "title": "Hwyla / Waila"},

    "jade": {"slug": "jade", "title": "Jade"},
    "玉": {"slug": "jade", "title": "Jade"},

    "暮色森林": {"slug": "twilight-forest", "cf": 227639, "title": "Twilight Forest"},
    "twilight forest": {"slug": "twilight-forest", "cf": 227639, "title": "Twilight Forest"},
    "twilight": {"slug": "twilight-forest", "cf": 227639, "title": "Twilight Forest"},

    "应用能源2": {"slug": "ae2", "cf": 223794, "title": "Applied Energistics 2"},
    "ae2": {"slug": "ae2", "cf": 223794, "title": "Applied Energistics 2"},
    "applied energistics 2": {"slug": "ae2", "cf": 223794, "title": "Applied Energistics 2"},

    "神秘时代": {"slug": "thaumcraft", "cf": 223628, "title": "Thaumcraft"},
    "thaumcraft": {"slug": "thaumcraft", "cf": 223628, "title": "Thaumcraft"},

    "植物魔法": {"slug": "botania", "cf": 225643, "title": "Botania"},
    "botania": {"slug": "botania", "cf": 225643, "title": "Botania"},

    "匠魂": {"slug": "tinkers-construct", "cf": 74072, "title": "Tinkers' Construct"},
    "tinkers construct": {"slug": "tinkers-construct", "cf": 74072, "title": "Tinkers' Construct"},
    "匠魂3": {"slug": "tinkers-construct-3", "title": "Tinkers' Construct 3"},

    "工业时代2": {"slug": "industrial-craft", "cf": 242638, "title": "IndustrialCraft 2"},
    "ic2": {"slug": "industrial-craft", "cf": 242638, "title": "IndustrialCraft 2"},
    "industrial craft": {"slug": "industrial-craft", "cf": 242638, "title": "IndustrialCraft 2"},

    "热力膨胀": {"slug": "thermal-expansion", "cf": 69163, "title": "Thermal Expansion"},
    "thermal expansion": {"slug": "thermal-expansion", "cf": 69163, "title": "Thermal Expansion"},
    "thermal系列": {"slug": "thermal-expansion", "cf": 69163, "title": "Thermal Expansion"},

    "铁路": {"slug": "railcraft", "cf": 227443, "title": "Railcraft"},
    "railcraft": {"slug": "railcraft", "cf": 227443, "title": "Railcraft"},

    "林业": {"slug": "forestry", "cf": 59751, "title": "Forestry"},
    "forestry": {"slug": "forestry", "cf": 59751, "title": "Forestry"},

    "星系": {"cf": 564236, "title": "Galacticraft Legacy"},
    "galacticraft": {"cf": 564236, "title": "Galacticraft Legacy"},

    "储物抽屉": {"slug": "storage-drawers", "cf": 223852, "title": "Storage Drawers"},
    "storage drawers": {"slug": "storage-drawers", "cf": 223852, "title": "Storage Drawers"},

    "循环": {"slug": "cyclic", "cf": 239286, "title": "Cyclic"},
    "cyclic": {"slug": "cyclic", "cf": 239286, "title": "Cyclic"},

    "更多箱子": {"slug": "iron-chests", "cf": 228756, "title": "Iron Chests"},
    "iron chests": {"slug": "iron-chests", "cf": 228756, "title": "Iron Chests"},
    "铁箱子": {"slug": "iron-chests", "cf": 228756, "title": "Iron Chests"},

    "背包": {"slug": "backpacked", "title": "Backpacked"},
    "旅行背包": {"slug": "travelers-backpack", "title": "Traveler's Backpack"},
    "traveler's backpack": {"slug": "travelers-backpack", "title": "Traveler's Backpack"},

    "苹果皮": {"slug": "appleskin", "title": "AppleSkin"},
    "appleskin": {"slug": "appleskin", "title": "AppleSkin"},
    "饥饿值显示": {"slug": "appleskin", "title": "AppleSkin"},

    "伤害显示": {"title": "ToroHealth Damage Indicators"},
    "torohealth": {"title": "ToroHealth Damage Indicators"},

    "方块信息": {"slug": "wthit", "title": "What Is That? (WTHIT)"},
    "wthit": {"slug": "wthit", "title": "What Is That? (WTHIT)"},
    "hud显示": {"slug": "wthit", "title": "What Is That? (WTHIT)"},

    "连锁采集": {"slug": "veinminer", "cf": 67133, "title": "VeinMiner"},
    "veinminer": {"slug": "veinminer", "cf": 67133, "title": "VeinMiner"},
    "vein miner": {"slug": "veinminer", "cf": 67133, "title": "VeinMiner"},

    "一键挖矿": {"slug": "veinminer", "cf": 67133, "title": "VeinMiner"},

    "投影": {"slug": "litematica", "title": "Litematica"},
    "litematica": {"slug": "litematica", "title": "Litematica"},

    "迷你地图": {"slug": "voxelmap", "cf": 225179, "title": "VoxelMap"},
    "voxelmap": {"slug": "voxelmap", "cf": 225179, "title": "VoxelMap"},

    "物品滚轮": {"slug": "inventory-profiles-next", "title": "Inventory Profiles Next"},
    "inventory profiles": {"slug": "inventory-profiles-next", "title": "Inventory Profiles Next"},

    "更好的F3": {"slug": "betterf3", "title": "BetterF3"},
    "betterf3": {"slug": "betterf3", "title": "BetterF3"},
    "better f3": {"slug": "betterf3", "title": "BetterF3"},

    "钠": {"slug": "sodium", "title": "Sodium"},
    "sodium": {"slug": "sodium", "title": "Sodium"},
    "铷": {"slug": "sodium", "title": "Sodium (Rubidium)"},

    "锂": {"slug": "lithium", "title": "Lithium"},
    "lithium": {"slug": "lithium", "title": "Lithium"},

    # Phosphor 已停更，光照优化统一指向 Starlight
    "磷": {"slug": "starlight", "title": "Starlight（Phosphor 已停更）"},
    "phosphor": {"slug": "starlight", "title": "Starlight（Phosphor 已停更）"},

    "星光": {"slug": "starlight", "title": "Starlight"},
    "starlight": {"slug": "starlight", "title": "Starlight"},

    "iris": {"slug": "iris", "title": "Iris Shaders"},
    "iris光影": {"slug": "iris", "title": "Iris Shaders"},
    "光影加载器": {"slug": "iris", "title": "Iris Shaders"},

    "oculus": {"slug": "oculus", "title": "Oculus"},
    "oculus光影": {"slug": "oculus", "title": "Oculus"},

    "优化": {"slug": "sodium-extra", "title": "Sodium Extra"},
    "sodium extra": {"slug": "sodium-extra", "title": "Sodium Extra"},

    "更好的结束": {"slug": "better-end", "title": "Better End"},
    "better end": {"slug": "better-end", "title": "Better End"},

    "更好的下界": {"slug": "better-nether", "title": "Better Nether"},
    "better nether": {"slug": "better-nether", "title": "Better Nether"},

    "夸克": {"slug": "quark", "cf": 243121, "title": "Quark"},
    "quark": {"slug": "quark", "cf": 243121, "title": "Quark"},

    "补充配件": {"slug": "supplementaries", "title": "Supplementaries"},
    "supplementaries": {"slug": "supplementaries", "title": "Supplementaries"},

    "农夫乐事": {"slug": "farmers-delight", "title": "Farmer's Delight"},
    "farmer's delight": {"slug": "farmers-delight", "title": "Farmer's Delight"},
    "farmers delight": {"slug": "farmers-delight", "title": "Farmer's Delight"},

    "中式工坊": {"slug": "chineseworkshop", "title": "ChineseWorkshop"},
    "chineseworkshop": {"slug": "chineseworkshop", "title": "ChineseWorkshop"},
    "中式建筑": {"slug": "chineseworkshop", "title": "ChineseWorkshop"},

    "创世神": {"slug": "worldedit", "cf": 225608, "title": "WorldEdit"},
    "worldedit": {"slug": "worldedit", "cf": 225608, "title": "WorldEdit"},
    "we": {"slug": "worldedit", "cf": 225608, "title": "WorldEdit"},

    "创世神CUI": {"slug": "worldeditcui", "title": "WorldEditCUI"},
    "worldeditcui": {"slug": "worldeditcui", "title": "WorldEditCUI"},

    "NEI": {"slug": "nei", "title": "Not Enough Items"},
    "not enough items": {"slug": "nei", "title": "Not Enough Items"},

    "伤害指示器": {"title": "ToroHealth Damage Indicators"},

    "地牢": {"slug": "dungeons-mod", "title": "Dungeons Mod"},
    "更多结构": {"slug": "yungs-better-dungeons", "title": "YUNG's Better Dungeons"},
    "yungs更好的地牢": {"slug": "yungs-better-dungeons", "title": "YUNG's Better Dungeons"},

    "更好的村庄": {"slug": "yungs-better-villages", "title": "YUNG's Better Villages"},
    "yungs更好的村庄": {"slug": "yungs-better-villages", "title": "YUNG's Better Villages"},

    "更好的矿井": {"slug": "yungs-better-mineshafts", "title": "YUNG's Better Mineshafts"},
    "yungs更好的矿井": {"slug": "yungs-better-mineshafts", "title": "YUNG's Better Mineshafts"},

    "更好的海洋": {"slug": "yungs-better-ocean-monuments", "title": "YUNG's Better Ocean Monuments"},
    "yungs更好的海洋神殿": {"slug": "yungs-better-ocean-monuments", "title": "YUNG's Better Ocean Monuments"},

    "更好的要塞": {"slug": "yungs-better-strongholds", "title": "YUNG's Better Strongholds"},
    "yungs更好的要塞": {"slug": "yungs-better-strongholds", "title": "YUNG's Better Strongholds"},

    "更好的桥梁": {"slug": "yungs-bridges", "title": "YUNG's Bridges"},

    "沉浸工程": {"slug": "immersive-engineering", "cf": 231951, "title": "Immersive Engineering"},
    "immersive engineering": {"slug": "immersive-engineering", "cf": 231951, "title": "Immersive Engineering"},

    "沉浸传送门": {"slug": "immersive-portals-mod", "title": "Immersive Portals"},
    "immersive portals": {"slug": "immersive-portals-mod", "title": "Immersive Portals"},

    "动态光源": {"slug": "lambdynamiclights", "title": "LambDynamicLights"},
    "dynamic lights": {"slug": "lambdynamiclights", "title": "LambDynamicLights"},

    "动态光源forge": {"slug": "dynamic-lights", "cf": 227874, "title": "Dynamic Lights"},

    "真实掉落": {"slug": "realistic-item-drops", "title": "Realistic Item Drops"},

    "声音滤波器": {"slug": "presence-footsteps", "title": "Presence Footsteps"},
    "脚步声": {"slug": "presence-footsteps", "title": "Presence Footsteps"},

    "万物皆可熔炼": {"slug": "all-in-one-oven", "title": "All In One Oven"},

    "更多熔炉": {"slug": "iron-furnaces", "title": "Iron Furnaces"},
    "铁熔炉": {"slug": "iron-furnaces", "title": "Iron Furnaces"},

    "更多傀儡": {"slug": "extra-golems", "title": "Extra Golems"},

    "更多生物": {"slug": "mowzies-mobs", "title": "Mowzie's Mobs"},
    "mowzies mobs": {"slug": "mowzies-mobs", "title": "Mowzie's Mobs"},

    "更多附魔": {"slug": "apotheosis", "cf": 313970, "title": "Apotheosis"},
    "apotheosis": {"slug": "apotheosis", "cf": 313970, "title": "Apotheosis"},

    "传说武器": {"slug": "mcdw", "title": "MCDungeons Weapons"},
    "更多武器": {"slug": "mcdw", "title": "MCDungeons Weapons"},

    "考古": {"title": "Archaeology"},

    "传送书": {"slug": "waystones", "title": "Waystones"},
    "waystones": {"slug": "waystones", "title": "Waystones"},

    "传送石碑": {"slug": "waystones", "title": "Waystones"},

    "传送门枪": {"slug": "portal-gun", "cf": 229084, "title": "Portal Gun"},

    "搬运": {"slug": "carry-on", "title": "Carry On"},
    "carry on": {"slug": "carry-on", "title": "Carry On"},

    "搬箱器": {"slug": "carry-on", "title": "Carry On"},

    "懒人厨房": {"slug": "cooking-for-blockheads", "title": "Cooking for Blockheads"},
    "cooking for blockheads": {"slug": "cooking-for-blockheads", "title": "Cooking for Blockheads"},

    "潘马斯农场": {"slug": "pam-harvestcraft-2", "cf": 221857, "title": "Pam's HarvestCraft 2"},
    "pam's harvestcraft": {"slug": "pam-harvestcraft-2", "cf": 221857, "title": "Pam's HarvestCraft 2"},
    "harvestcraft": {"slug": "pam-harvestcraft-2", "cf": 221857, "title": "Pam's HarvestCraft 2"},

    "糖果世界": {"title": "Candy World"},

    "梦幻世界": {"slug": "twilight-forest", "cf": 227639, "title": "Twilight Forest"},

    "ATC警报": {"slug": "atc-alarm", "title": "ATC Alarm"},

    "经验书": {"title": "XP Book"},
    "xp book": {"title": "XP Book"},

    "通用机械": {"slug": "mekanism", "cf": 268560, "title": "Mekanism"},
    "mekanism": {"slug": "mekanism", "cf": 268560, "title": "Mekanism"},

    "Mekanism": {"slug": "mekanism", "cf": 268560, "title": "Mekanism"},
    "通用机械发电机": {"slug": "mekanism-generators", "cf": 268566, "title": "Mekanism Generators"},
    "通用机械附加": {"slug": "mekanism-tools", "cf": 268567, "title": "Mekanism Tools"},

    "深度学习": {"slug": "deep-learning", "title": "Deep Learning"},

    "更好的进度": {"slug": "better-advancements", "title": "Better Advancements"},
    "better advancements": {"slug": "better-advancements", "title": "Better Advancements"},

    "更好的食谱": {"slug": "roughly-enough-professions", "title": "Roughly Enough Professions"},
    "roughly enough professions": {"slug": "roughly-enough-professions", "title": "Roughly Enough Professions"},

    "更好的交易": {"slug": "better-trades", "title": "Better Trades"},
    "better trades": {"slug": "better-trades", "title": "Better Trades"},

    "村民交易": {"slug": "easy-villagers", "title": "Easy Villagers"},
    "easy villagers": {"slug": "easy-villagers", "title": "Easy Villagers"},

    "刷怪塔": {"slug": "mob-grinding-utils", "title": "Mob Grinding Utils"},

    "经验增益": {"slug": "xp-berry", "title": "XP Berry"},
    "经验浆果": {"slug": "xp-berry", "title": "XP Berry"},

    "加速火把": {"slug": "torchmaster", "title": "Torchmaster"},
    "torchmaster": {"slug": "torchmaster", "title": "Torchmaster"},

    "刷怪笼": {"slug": "apotheosis", "cf": 313970, "title": "Apotheosis"},

    "末影接口": {"slug": "ender-io", "cf": 64578, "title": "Ender IO"},
    "ender io": {"slug": "ender-io", "cf": 64578, "title": "Ender IO"},

    "末影存储": {"slug": "ender-storage-1-8", "cf": 245174, "title": "Ender Storage"},
    "ender storage": {"slug": "ender-storage-1-8", "cf": 245174, "title": "Ender Storage"},

    "末影箱子": {"slug": "ender-chests", "title": "Ender Chests"},

    "传输管道": {"slug": "xnet", "title": "XNet"},
    "xnet": {"slug": "xnet", "title": "XNet"},

    "管道": {"slug": "pipez", "title": "Pipez"},
    "pipez": {"slug": "pipez", "title": "Pipez"},

    "漏斗": {"slug": "hopper", "title": "Hopper"},
    "更多漏斗": {"slug": "hopper", "title": "Hopper"},

    "构建工艺": {"slug": "buildcraft", "cf": 61811, "title": "BuildCraft"},
    "buildcraft": {"slug": "buildcraft", "cf": 61811, "title": "BuildCraft"},

    "电脑": {"slug": "computercraft", "cf": 225604, "title": "ComputerCraft"},
    "computercraft": {"slug": "computercraft", "cf": 225604, "title": "ComputerCraft"},
    "cc": {"slug": "computercraft", "cf": 225604, "title": "ComputerCraft"},

    "开放式电脑": {"slug": "opencomputers", "cf": 225658, "title": "OpenComputers"},
    "opencomputers": {"slug": "opencomputers", "cf": 225658, "title": "OpenComputers"},

    "更好的FPS": {"cf": 229876, "title": "BetterFps"},

    "显示FPS": {"cf": 229876, "title": "BetterFps"},

    "按键显示": {"slug": "keystrokes", "title": "Keystrokes"},
    "keystrokes": {"slug": "keystrokes", "title": "Keystrokes"},

    "CPS显示": {"slug": "cps", "title": "CPS"},

    "皮肤显示": {"slug": "3dskinlayers", "title": "3D Skin Layers"},
    "3d皮肤层": {"slug": "3dskinlayers", "title": "3D Skin Layers"},
    "3d skin layers": {"slug": "3dskinlayers", "title": "3D Skin Layers"},

    "自定义皮肤": {"slug": "customskinloader", "title": "CustomSkinLoader"},
    "customskinloader": {"slug": "customskinloader", "title": "CustomSkinLoader"},
    "csl": {"slug": "customskinloader", "title": "CustomSkinLoader"},

    "万用皮肤": {"slug": "customskinloader", "title": "CustomSkinLoader"},

    "语音": {"slug": "simple-voice-chat", "title": "Simple Voice Chat"},
    "simple voice chat": {"slug": "simple-voice-chat", "title": "Simple Voice Chat"},
    "语音聊天": {"slug": "simple-voice-chat", "title": "Simple Voice Chat"},

    "真实地形": {"slug": "terralith", "title": "Terralith"},
    "terralith": {"slug": "terralith", "title": "Terralith"},

    "更好的地形": {"slug": "terralith", "title": "Terralith"},

    "更好的末地": {"slug": "better-end", "title": "Better End"},

    "更好的下界地形": {"slug": "betternether", "title": "BetterNether"},

    "生物群系": {"slug": "biomes-o-plenty", "cf": 220318, "title": "Biomes O' Plenty"},
    "biomes o plenty": {"slug": "biomes-o-plenty", "cf": 220318, "title": "Biomes O' Plenty"},
    "bop": {"slug": "biomes-o-plenty", "cf": 220318, "title": "Biomes O' Plenty"},

    "超多生物群系": {"slug": "biomes-o-plenty", "cf": 220318, "title": "Biomes O' Plenty"},

    "改进生物群系": {"slug": "byg", "title": "Oh The Biomes You'll Go"},
    "byg": {"slug": "byg", "title": "Oh The Biomes You'll Go"},
    "oh the biomes you'll go": {"slug": "byg", "title": "Oh The Biomes You'll Go"},

    "地物": {"slug": "regions-unexplored", "title": "Regions Unexplored"},
    "regions unexplored": {"slug": "regions-unexplored", "title": "Regions Unexplored"},

    "奇怪的装饰": {"slug": "quark", "cf": 243121, "title": "Quark"},

    "更多的门": {"slug": "more-doors", "title": "More Doors"},

    "家具": {"slug": "furniture-mod", "cf": 229088, "title": "Furniture Mod"},
    "家具mod": {"slug": "furniture-mod", "cf": 229088, "title": "Furniture Mod"},

    "MrCrayfish的家具": {"slug": "furniture-mod", "cf": 229088, "title": "MrCrayfish's Furniture Mod"},

    "枪械": {"slug": "mrcrayfishs-gun-mod", "cf": 228832, "title": "MrCrayfish's Gun Mod"},
    "CGWM": {"slug": "mrcrayfishs-gun-mod", "cf": 228832, "title": "MrCrayfish's Gun Mod"},

    "维克的现代战争": {"slug": "vic-modern-warfare", "title": "Vic's Modern Warfare"},
    "vic's modern warfare": {"slug": "vic-modern-warfare", "title": "Vic's Modern Warfare"},

    "现代战争": {"slug": "vic-modern-warfare", "title": "Vic's Modern Warfare"},

    "古代战争": {"slug": "ancient-warfare", "title": "Ancient Warfare"},

    "史诗战斗": {"slug": "epic-fight", "title": "Epic Fight"},
    "epic fight": {"slug": "epic-fight", "title": "Epic Fight"},
    "史诗格斗": {"slug": "epic-fight", "title": "Epic Fight"},

    "更好的战斗": {"slug": "better-combat", "title": "Better Combat"},
    "better combat": {"slug": "better-combat", "title": "Better Combat"},

    "更多武器mod": {"slug": "weapon-mod", "title": "Weapon Mod"},

    "钻石工具": {"slug": "diamond-tools", "title": "Diamond Tools"},

    "更多工具": {"slug": "tool-belt", "title": "Tool Belt"},

    "更多盔甲": {"slug": "armor-plus", "title": "Armor Plus"},

    "更多附魔书": {"slug": "spellbound", "title": "Spellbound"},

    "魔法": {"slug": "ars-nouveau", "title": "Ars Nouveau"},
    "ars nouveau": {"slug": "ars-nouveau", "title": "Ars Nouveau"},
    "新生魔法": {"slug": "ars-nouveau", "title": "Ars Nouveau"},

    "巫术": {"title": "Witchcraft"},
    "witchcraft": {"title": "Witchcraft"},

    "血魔法": {"slug": "blood-magic", "cf": 224791, "title": "Blood Magic"},
    "blood magic": {"slug": "blood-magic", "cf": 224791, "title": "Blood Magic"},

    "星辉魔法": {"slug": "astral-sorcery", "cf": 241721, "title": "Astral Sorcery"},
    "astral sorcery": {"slug": "astral-sorcery", "cf": 241721, "title": "Astral Sorcery"},

    "自然灵气": {"slug": "nature-essence", "title": "Nature's Essence"},

    "元素工艺": {"slug": "elemental-craft", "title": "Elemental Craft"},

    "神秘农业": {"slug": "mystical-agriculture", "cf": 246640, "title": "Mystical Agriculture"},
    "mystical agriculture": {"slug": "mystical-agriculture", "cf": 246640, "title": "Mystical Agriculture"},

    "神秘农业扩展": {"slug": "mystical-agriculture-extensions", "title": "Mystical Agriculture Extensions"},

    "矿石作物": {"slug": "mystical-agriculture", "cf": 246640, "title": "Mystical Agriculture"},

    "懒人农业": {"slug": "simple-farming", "title": "Simple Farming"},
    "simple farming": {"slug": "simple-farming", "title": "Simple Farming"},

    "更多农作物": {"slug": "pam-harvestcraft-2", "cf": 221857, "title": "Pam's HarvestCraft 2"},

    "集装箱": {"slug": "shulker-box-tooltip", "title": "Shulker Box Tooltip"},

    "潜影盒预览": {"slug": "shulker-box-tooltip", "title": "Shulker Box Tooltip"},
    "shulker box tooltip": {"slug": "shulker-box-tooltip", "title": "Shulker Box Tooltip"},

    "潜影盒": {"slug": "iron-shulker-boxes", "title": "Iron Shulker Boxes"},
    "iron shulker boxes": {"slug": "iron-shulker-boxes", "title": "Iron Shulker Boxes"},

    "打包": {"slug": "packing-tape", "title": "Packing Tape"},

    "区块加载": {"slug": "chunk-pregenerator", "title": "Chunk Pregenerator"},
    "chunk pregenerator": {"slug": "chunk-pregenerator", "title": "Chunk Pregenerator"},
    "预生成区块": {"slug": "chunk-pregenerator", "title": "Chunk Pregenerator"},

    "多世界": {"slug": "multiverse", "title": "MultiVerse"},
    "multiverse": {"slug": "multiverse", "title": "MultiVerse"},
    "多世界管理": {"slug": "multiverse", "title": "MultiVerse"},

    "区域保护": {"slug": "worldguard", "title": "WorldGuard"},
    "worldguard": {"slug": "worldguard", "title": "WorldGuard"},

    "领地": {"slug": "griefprevention", "title": "GriefPrevention"},
    "griefprevention": {"slug": "griefprevention", "title": "GriefPrevention"},

    "登录": {"slug": "authme", "title": "AuthMe"},
    "authme": {"slug": "authme", "title": "AuthMe"},

    "皮肤站": {"slug": "blessing-skin", "title": "Blessing Skin"},

    "更好的钓鱼": {"slug": "better-fishing", "title": "Better Fishing"},
    "better fishing": {"slug": "better-fishing", "title": "Better Fishing"},

    "更好的下雨": {"slug": "better-weather", "title": "Better Weather"},
    "better weather": {"slug": "better-weather", "title": "Better Weather"},

    "更好的雪": {"slug": "snow-realistic", "title": "Snow! Realistic"},

    "更好的树叶": {"slug": "better-leaves", "title": "Better Leaves"},

    "真实掉落": {"slug": "realistic-item-drops", "title": "Realistic Item Drops"},

    "经验掉落": {"slug": "experience-bottling", "title": "Experience Bottling"},
    "附魔书": {"slug": "experience-bottling", "title": "Experience Bottling"},
}

# 别名索引（小写化）
_MOD_ALIASES_LOWER = {k.lower(): v for k, v in MOD_ALIASES.items()}


def lookup_mod_alias(query: str):
    """查找中文别名，返回匹配的 Modrinth slug 和 CurseForge ID。

    返回 (slug, cf_id, title) 或 (None, None, None)。
    """
    key = query.strip().lower()
    if key in _MOD_ALIASES_LOWER:
        info = _MOD_ALIASES_LOWER[key]
        return info.get("slug"), info.get("cf"), info.get("title", key)
    return None, None, None


def fuzzy_match_mod(query: str):
    """模糊匹配中文别名：返回所有匹配项列表。

    对每个匹配项返回 (slug, cf_id, title, match_key)。
    """
    key = query.strip().lower()
    results = []
    for alias, info in _MOD_ALIASES_LOWER.items():
        if key in alias or alias in key:
            results.append((
                info.get("slug"),
                info.get("cf"),
                info.get("title", alias),
                alias,
            ))
    return results


# ----------------------------------------------------------------
# 热门整合包中文别名
# ----------------------------------------------------------------
MODPACK_ALIASES = {
    "GTNH": {"cf": 252507, "title": "GregTech: New Horizons"},
    "格雷科技新视野": {"cf": 252507, "title": "GregTech: New Horizons"},
    "gregtech new horizons": {"cf": 252507, "title": "GregTech: New Horizons"},
    "gt new horizons": {"cf": 252507, "title": "GregTech: New Horizons"},

    "RLCraft": {"cf": 285109, "title": "RLCraft"},
    "真实生存": {"cf": 285109, "title": "RLCraft"},

    "更好的MC": {"cf": 429793, "title": "Better MC"},
    "better mc": {"cf": 429793, "title": "Better MC"},
    "better minecraft": {"cf": 429793, "title": "Better MC"},

    "空岛": {"slug": "simply-skyblock", "title": "Simply Skyblock"},
    "skyblock": {"slug": "simply-skyblock", "title": "Simply Skyblock"},
    "空岛生存": {"slug": "simply-skyblock", "title": "Simply Skyblock"},

    # CBC = 黄铜协奏曲（Create The Brass Concerto），Forge 1.20.1。
    "机械动力": {"cf": CBC_CF_ID, "title": "机械动力：黄铜协奏曲"},
    "机械动力整合包": {"cf": CBC_CF_ID, "title": "机械动力：黄铜协奏曲"},
    "机械动力整合": {"cf": CBC_CF_ID, "title": "机械动力：黄铜协奏曲"},
    "黄铜协奏曲": {"cf": CBC_CF_ID, "title": "机械动力：黄铜协奏曲"},
    "cbc": {"cf": CBC_CF_ID, "title": "机械动力：黄铜协奏曲"},
    "brass concerto": {"cf": CBC_CF_ID, "title": "机械动力：黄铜协奏曲"},
    "create the brass concerto": {"cf": CBC_CF_ID, "title": "机械动力：黄铜协奏曲"},
    "create-the-brass-concerto": {"cf": CBC_CF_ID, "title": "机械动力：黄铜协奏曲"},

    "齿轮盛宴": {"cf": CDC_CF_ID, "title": "机械动力：齿轮盛宴"},
    "cdc": {"cf": CDC_CF_ID, "title": "机械动力：齿轮盛宴"},
    "create delight": {"cf": CDC_CF_ID, "title": "机械动力：齿轮盛宴"},
    "create delight remake": {"cf": CDC_CF_ID, "title": "机械动力：齿轮盛宴"},
    "create: delight remake": {"cf": CDC_CF_ID, "title": "机械动力：齿轮盛宴"},

    "create+": {"slug": "create_plus", "title": "Create+"},
    "create plus": {"slug": "create_plus", "title": "Create+"},
    "createplus": {"slug": "create_plus", "title": "Create+"},
    "机械动力+": {"slug": "create_plus", "title": "Create+"},
}

_MODPACK_ALIASES_LOWER = {k.lower(): v for k, v in MODPACK_ALIASES.items()}


def lookup_modpack_alias(query: str):
    """查找整合包中文别名。"""
    key = query.strip().lower()
    if key in _MODPACK_ALIASES_LOWER:
        info = _MODPACK_ALIASES_LOWER[key]
        return info.get("slug"), info.get("cf"), info.get("title", key)
    return None, None, None


# ----------------------------------------------------------------
# 热门推荐（供“一键安装”/热门列表使用）
# 每项: (显示名, 来源, 键值, MC版本, 加载器)
#  来源 modrinth -> 键值是 slug
#  来源 curseforge -> 键值是 addonId
#  mc 为空表示不限制版本（用实例当前版本）
# ----------------------------------------------------------------
POPULAR_MODS = [
    # 优化类（mc 一律 None：随实例当前版本，不绑死旧版本）
    ("Sodium 钠 (渲染优化)", "modrinth", "sodium", None, "fabric"),
    ("Lithium 锂 (服务器优化)", "modrinth", "lithium", None, "fabric"),
    ("Starlight 星光 (光照优化)", "modrinth", "starlight", None, "fabric"),
    ("Iris 光影加载器", "modrinth", "iris", None, "fabric"),
    ("BetterF3", "modrinth", "betterf3", None, "fabric"),
    # 功能类
    ("JEI 物品管理器", "modrinth", "jei", None, "forge"),
    ("REI 物品管理器", "modrinth", "rei", None, "fabric"),
    ("EMI 物品管理器", "modrinth", "emi", None, "fabric"),
    ("Xaero's 小地图", "modrinth", "xaeros-minimap", None, "fabric"),
    ("Xaero's 世界地图", "modrinth", "xaeros-world-map", None, "fabric"),
    ("JourneyMap 旅行地图", "curseforge", 32274, None, "forge"),
    ("AppleSkin 饥饿值显示", "modrinth", "appleskin", None, "fabric"),
    ("WTHIT 方块信息", "modrinth", "wthit", None, "fabric"),
    ("Litematica 投影", "modrinth", "litematica", None, "fabric"),
    ("VeinMiner 连锁采集", "curseforge", 67133, None, "forge"),
    ("WorldEdit 创世神", "modrinth", "worldedit", None, "forge"),
    ("Carry On 搬运", "modrinth", "carry-on", None, "fabric"),
    # 内容类
    ("Twilight Forest 暮色森林", "curseforge", 227639, None, "forge"),
    ("Applied Energistics 2 AE2", "curseforge", 223794, None, "forge"),
    ("Botania 植物魔法", "curseforge", 225643, None, "forge"),
    ("Tinkers' Construct 匠魂", "curseforge", 74072, None, "forge"),
    ("IndustrialCraft 2 工业", "curseforge", 242638, None, "forge"),
    ("Mekanism 通用机械", "curseforge", 268560, None, "forge"),
    ("Thermal Expansion 热力", "curseforge", 69163, None, "forge"),
    ("Create 机械动力", "modrinth", "create", None, "fabric"),
    ("Farmer's Delight 农夫乐事", "modrinth", "farmers-delight", None, "fabric"),
    ("Quark 夸克", "curseforge", 243121, None, "forge"),
    ("Ice and Fire 冰火传说", "curseforge", 264231, None, "forge"),
    ("Galacticraft Legacy 星系", "curseforge", 564236, None, "forge"),
    ("Biomes O' Plenty 超多群系", "curseforge", 220318, None, "forge"),
    ("Mowzie's Mobs 更多生物", "modrinth", "mowzies-mobs", None, "forge"),
    # 辅助类
    ("CustomSkinLoader 皮肤加载", "modrinth", "customskinloader", None, "fabric"),
    ("3D Skin Layers 皮肤层", "modrinth", "3dskinlayers", None, "fabric"),
    ("Simple Voice Chat 语音", "modrinth", "simple-voice-chat", None, "fabric"),
    ("Inventory Profiles 物品栏", "modrinth", "inventory-profiles-next", None, "fabric"),
    ("Path of Exile 音效", "modrinth", "presence-footsteps", None, "fabric"),
]

POPULAR_MODPACKS = [
    # (显示名, 来源, 键值, curseforge_slug)
    ("机械动力：黄铜协奏曲 CBC 1.20.1", "curseforge", CBC_CF_ID, CBC_CF_SLUG),
    ("机械动力：齿轮盛宴 CDC 1.20.1", "curseforge", CDC_CF_ID, CDC_CF_SLUG),
    ("RLCraft 真实生存", "curseforge", 285109, "rlcraft"),
    ("Better MC 更好的MC", "curseforge", 429793, "better-mc-forge-bmc1"),
    ("Simply Skyblock 空岛", "modrinth", "simply-skyblock", None),
    ("Create+（原版风 1.19.2，不是CBC）", "modrinth", "create_plus", None),
    ("GTNH 格雷科技新视野", "curseforge", 252507, "gt-new-horizons"),
    ("Fabulously Optimized", "modrinth", "fabulously-optimized", None),
    ("Adrenaline", "modrinth", "adrenaline", None),
    ("All Of Create", "modrinth", "all-of-create-fabric", None),
]
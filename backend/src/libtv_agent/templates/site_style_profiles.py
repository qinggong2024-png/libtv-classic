from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Literal

SiteProfileStatus = Literal["draft", "active", "retired"]
EvidenceLevel = Literal["verified_site", "restricted_site"]
LibraryScope = Literal["general", "brand"]


@dataclass(frozen=True)
class SiteStyleProfile:
    id: str
    version: str
    status: SiteProfileStatus
    name: str
    site_url: str
    source_urls: tuple[str, ...]
    retrieved_at: str
    evidence_level: EvidenceLevel
    tone: str
    audience: str
    palette: tuple[str, ...]
    typography: str
    camera_language: str
    opening_rule: str
    middle_rule: str
    ending_rule: str
    preferred_shots: tuple[str, ...]
    preferred_actions: tuple[str, ...]
    pacing: str
    product_priorities: tuple[str, ...]
    forbidden_mismatches: tuple[str, ...]
    research_note: str
    expression_language: str = "自然、克制并服务于商品展示"
    library_scope: LibraryScope = "brand"
    brand_id: str | None = None
    motion_energy: str = "standard"
    primary_motion_pool: tuple[str, ...] = ()
    micro_motion_pool: tuple[str, ...] = ()
    expression_pool: tuple[str, ...] = ()
    camera_pool: tuple[str, ...] = ()
    forbidden_motion: tuple[str, ...] = ()
    preferred_environment: tuple[str, ...] = ()
    product_risk_rules: tuple[str, ...] = ()
    dedupe_recent_count: int = 5
    dedupe_combo_window: int = 8
    dedupe_family_window: int = 10
    dedupe_family_limit: int = 3

    def __post_init__(self) -> None:
        # Older catalog records only have preferred_actions and text pools.
        # Materialize compatible defaults so every profile can use the selector.
        if not self.expression_pool:
            object.__setattr__(self, "expression_pool", (self.expression_language,))
        if not self.camera_pool:
            object.__setattr__(self, "camera_pool", (self.camera_language,))
        if not self.forbidden_motion:
            object.__setattr__(self, "forbidden_motion", self.forbidden_mismatches)
        if not self.product_risk_rules:
            object.__setattr__(self, "product_risk_rules", self.product_priorities)

    def to_public_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["siteUrl"] = value.pop("site_url")
        value["sourceUrls"] = value.pop("source_urls")
        value["retrievedAt"] = value.pop("retrieved_at")
        value["evidenceLevel"] = value.pop("evidence_level")
        value["cameraLanguage"] = value.pop("camera_language")
        value["openingRule"] = value.pop("opening_rule")
        value["middleRule"] = value.pop("middle_rule")
        value["endingRule"] = value.pop("ending_rule")
        value["preferredShots"] = value.pop("preferred_shots")
        value["preferredActions"] = value.pop("preferred_actions")
        value["productPriorities"] = value.pop("product_priorities")
        value["forbiddenMismatches"] = value.pop("forbidden_mismatches")
        value["researchNote"] = value.pop("research_note")
        value["expressionLanguage"] = value.pop("expression_language")
        value["libraryScope"] = value.pop("library_scope")
        brand_id = value.pop("brand_id")
        value["brandId"] = (brand_id or self.id) if self.library_scope == "brand" else None
        value["motionEnergy"] = value.pop("motion_energy")
        value["primaryMotionPool"] = value.pop("primary_motion_pool") or self.preferred_actions
        value["microMotionPool"] = value.pop("micro_motion_pool")
        value["expressionPool"] = value.pop("expression_pool")
        value["cameraPool"] = value.pop("camera_pool")
        value["forbiddenMotion"] = value.pop("forbidden_motion")
        value["preferredEnvironment"] = value.pop("preferred_environment")
        value["productRiskRules"] = value.pop("product_risk_rules")
        value["dedupeRecentCount"] = value.pop("dedupe_recent_count")
        value["dedupeComboWindow"] = value.pop("dedupe_combo_window")
        value["dedupeFamilyWindow"] = value.pop("dedupe_family_window")
        value["dedupeFamilyLimit"] = value.pop("dedupe_family_limit")
        return value


_RETRIEVED_AT = "2026-07-30"


SITE_STYLE_PROFILES: dict[str, SiteStyleProfile] = {
    "geeksoutfit": SiteStyleProfile(
        id="geeksoutfit",
        version="1.2.1",
        status="active",
        name="极客 / GeeksOutfit",
        site_url="https://www.geeksoutfit.com/",
        source_urls=(
            "https://www.geeksoutfit.com/",
            "https://www.instagram.com/geeksoutfitstore/",
            "https://www.tiktok.com/@geeksoutfitstore",
            "https://www.youtube.com/channel/UCYftwHKqZQuA9BDRUaatzaA",
        ),
        retrieved_at=_RETRIEVED_AT,
        evidence_level="verified_site",
        tone="轻快、聪明、带怀旧科幻玩味感，但保留商品陈列的清晰度",
        expression_language=(
            "轻松好奇，带一点聪明玩味的笑意，像自然分享一个有趣发现。"
        ),
        audience="喜欢 geek、nerdy、复古音乐和科幻文化的消费者",
        palette=("white", "black", "vintage accent colors"),
        typography="Space Mono / Ubuntu 风格的等宽与几何无衬线",
        camera_language="明亮平视观察为主，允许一次轻快推进、一次小幅横向跟随和利落细节切换，不用高速甩镜",
        opening_rule="前 3 秒先以整身中景建立服装与人物，再开始位移",
        middle_rule="以机灵掠视或自然微笑配合一次三分之四转身，并给印花、文字或袖口一次稳定细节停留",
        ending_rule="回到整件商品清晰可见的中景，以轻松定姿结束",
        preferred_shots=("整身中景", "三分之四中近景", "印花局部特写"),
        preferred_actions=(
            "controlled_walk",
            "three_quarter_turn",
            "print_detail_pause",
            "hem_adjust",
        ),
        pacing="明快机灵，像自然分享一个有趣发现",
        product_priorities=("整件版型", "印花和文字可读性", "复古色彩", "袖口和下摆"),
        forbidden_mismatches=(),
        research_note="首页和公开社媒入口可直接观察；已按用户确认的不变量边界纳入生产。",
    ),
    "guidingcross": SiteStyleProfile(
        id="guidingcross",
        version="1.0.1",
        status="active",
        name="基督 / GuidingCross",
        site_url="https://www.guidingcross.com/",
        source_urls=(
            "https://www.guidingcross.com/",
            "https://www.instagram.com/guidingcrossofficial",
            "https://www.tiktok.com/@guidingcrossclothing",
            "https://www.youtube.com/@guidingcross",
        ),
        retrieved_at=_RETRIEVED_AT,
        evidence_level="verified_site",
        tone="克制、温和、尊重信仰，文字先于动作",
        expression_language=(
            "真诚平和，温暖而安静，带着希望感。"
        ),
        audience="基督信仰服饰和日常穿搭用户",
        palette=("white", "deep navy gray", "washed garment tones"),
        typography="Poppins 风格的清楚无衬线",
        camera_language="柔和眼平正面观察与缓慢推进，镜头保持尊重、安定且不做突兀视角跳变",
        opening_rule="以正面或轻三分之四中景开场，让信仰文字先被读清",
        middle_rule="用平和视线和一次低幅度姿态变化展示水洗质感、衣摆或袖口，不遮挡核心文字",
        ending_rule="回到正面稳定停留，或自然低头后抬眼结束",
        preferred_shots=("正面中景", "稳定三分之四中景", "胸前文字近景"),
        preferred_actions=(
            "stance_shift",
            "controlled_walk",
            "three_quarter_turn",
            "print_detail_pause",
        ),
        pacing="舒缓安定，温和从容",
        product_priorities=("信仰文字可读性", "水洗面料质感", "完整穿搭", "版型稳定"),
        forbidden_mismatches=(),
        product_risk_rules=("信仰文字完整可读", "水洗面料与版型稳定", "已有道具不遮挡核心文字"),
        research_note=(
            "首页 mission 与产品主题可直接观察；宗教动作不默认加入，只使用自然站姿和有限手部动作。"
        ),
    ),
    "nerdywave": SiteStyleProfile(
        id="nerdywave",
        version="1.2.1",
        status="active",
        name="暗黑 / NerdyWave",
        site_url="https://www.nerdywave.com/",
        source_urls=(
            "https://www.nerdywave.com/",
            "https://www.instagram.com/nerdywave_apparel/",
            "https://www.tiktok.com/@nerdywave_apparel",
            "https://www.youtube.com/channel/UCqHz2Agwp4JCUh3pxD-6eYw",
        ),
        retrieved_at="2026-08-17",
        evidence_level="verified_site",
        tone="暗黑另类、复古 fandom、恐怖与神秘学母题并存；以黑色材质层次和图形可读性为核心",
        expression_language=(
            "冷静自信，带一点克制的玩味。"
        ),
        audience="偏好恐怖、神秘学、cryptid、cult film、游戏与暗黑街头图形服饰的用户",
        palette=("black", "white", "muted red", "washed gray"),
        typography="Instrument Sans 主体字，搭配高对比衬线展示字和手写标志",
        camera_language="以眼平或极轻低机位的侧面、三分之四中景建立张力；允许一次克制推进，清楚轮廓后再切印花近景",
        opening_rule="先以侧面或三分之四中景建立暗色轮廓和完整上装，不用闪烁特效",
        middle_rule=(
            "沿用受控转身或走动骨架，以稳定直视和明确肩线展示 mesh、水洗层次与图形，"
            "并保留印花可读停留"
        ),
        ending_rule="停在清晰轮廓与低饱和层次仍可辨认的稳定画面",
        preferred_shots=("侧面中景", "有限低机位", "印花近景"),
        preferred_actions=(
            "controlled_walk",
            "three_quarter_turn",
            "controlled_gaze",
            "print_detail_pause",
        ),
        pacing="冷感克制，有轻微态度和玩味",
        product_priorities=(
            "mesh jersey 与水洗材质",
            "黑白/暗红层次",
            "恐怖与 fandom 图形可读性",
            "完整轮廓",
        ),
        forbidden_mismatches=(),
        research_note=(
            "2026-08-17 通过 Chrome MCP 直访首页复核：黑底高对比、Instrument Sans、mesh jersey、"
            "水洗单品及 occult/cryptid/horror/gaming/film 分类均可直接观察；"
            "动作仍只继承已验证通用骨架。"
        ),
    ),
    "pagewings": SiteStyleProfile(
        id="pagewings",
        version="1.2.1",
        status="active",
        name="泛读书 / PageWings",
        site_url="https://www.pagewings.com/",
        source_urls=(
            "https://www.pagewings.com/",
            "https://www.instagram.com/pagewings_brand/",
            "https://www.youtube.com/watch?v=8t6Rbf0K4BU",
            "https://www.youtube.com/watch?v=E3HB8sxEe2c",
        ),
        retrieved_at="2026-08-17",
        evidence_level="verified_site",
        tone="明亮、活泼、彩色复古图形感；文学、公共图书馆与人权是商品主题，不等于安静学院风",
        expression_language=(
            "明亮亲和，像爱读书的人轻松分享自己的穿搭。"
        ),
        audience="书迷、公共图书馆支持者、文学与人权主题图形服饰用户",
        palette=(
            "white",
            "ink black",
            "sunflower yellow",
            "garden green",
            "colorful vintage graphics",
        ),
        typography="Lato 正文，搭配大号紧凑展示字和手写式主题字",
        camera_language="以明亮眼平的正面中景和中近景清楚切换；允许低幅走动、轻推与图形近景，每次近景后回到完整商品",
        opening_rule="先以正面中景建立完整上装和核心书籍、图书馆或人权主题图形",
        middle_rule="用一次低幅走动或三分之四转身衔接印花近景；已有书本时才允许简短阅读关系镜头",
        ending_rule="回到整件商品与主题文字都清晰可读的正面或轻侧中景",
        preferred_shots=("正面中景", "印花中近景", "整身或侧背商品确认", "已有书本关系镜头"),
        preferred_actions=(
            "stance_shift",
            "controlled_walk",
            "print_detail_pause",
            "print_indicate_release",
        ),
        pacing="明亮轻快，有轻松的分享感",
        product_priorities=(
            "书籍/图书馆/人权主题可读性",
            "彩色复古图形",
            "水洗材质与完整版型",
            "正背面商品确认",
        ),
        forbidden_mismatches=(),
        research_note=(
            "2026-08-17 通过 Chrome MCP 直访首页复核：Book Lovers、Banned Books、Public Libraries、"
            "Human Rights、Lato 字体和彩色生活方式横幅均可直接观察；"
            "官方样片已纠正旧的安静学院风假设。"
        ),
    ),
    "teachersgram": SiteStyleProfile(
        id="teachersgram",
        version="1.0.1",
        status="active",
        name="教师 / Teachersgram",
        site_url="https://www.teachersgram.com/",
        source_urls=(
            "https://www.teachersgram.com/",
            "https://www.instagram.com/teachersgramofficial/",
        ),
        retrieved_at=_RETRIEVED_AT,
        evidence_level="verified_site",
        tone="热情洋溢、活泼、明亮、亲和，同时保留教师日常的可信与实用感",
        expression_language=(
            "热情洋溢，像真实的老师自然分享自己的穿搭。"
        ),
        audience="教师和教育主题周边消费者",
        palette=("warm cream", "black", "soft classroom accents"),
        typography="Almarai / Arapey，正文清楚、标题有一点编辑感",
        camera_language="明亮有活力的朋友记录视角，允许短距离自然跟随、轻快横移和一次胸前细节推进",
        opening_rule="以热情对视、自然站姿或一到两步轻快走动建立整件服装和亲和感",
        middle_rule="以明亮微笑配合轻快三分之四侧身，让胸前图案朝向镜头，再展示一次袖口或下摆",
        ending_rule="停在教育主题文字清晰可读的中景，以自然微笑或放松表情结束",
        preferred_shots=("整身中景", "胸前图案中近景", "袖口/下摆细节"),
        preferred_actions=(
            "controlled_walk",
            "three_quarter_turn",
            "print_detail_pause",
            "hem_adjust",
        ),
        pacing="热情活泼，像真实的老师分享日常穿搭",
        product_priorities=("教育主题文字", "可穿着的版型", "胸前印花", "实用细节"),
        forbidden_mismatches=(),
        product_risk_rules=("教育主题文字完整可读", "真实可穿版型与实用细节", "已有道具不遮挡胸前图形"),
        research_note="首页分类 Math/Literature/Music/Art/Science 与暖奶油色、字体可直接观察。",
    ),
    "goodnurselife": SiteStyleProfile(
        id="goodnurselife",
        version="0.2.2",
        status="active",
        name="护士 / GoodNurseLife",
        site_url="https://www.goodnurselife.com/",
        source_urls=(
            "https://www.goodnurselife.com/",
            "https://www.facebook.com/profile.php?id=61559705181293",
            "https://www.instagram.com/nursehutshop/",
        ),
        retrieved_at="2026-08-17",
        evidence_level="verified_site",
        tone="柔和、亲切、带职业幽默与小型配件的轻快感；专科和职称信息必须清楚",
        expression_language=(
            "亲切可靠，像护理人员下班后轻松分享自己的穿搭。"
        ),
        audience="护士、影像科、牙科、检验科等医疗岗位及其职业主题周边用户",
        palette=("warm ivory", "pastel pink", "soft blue", "floral green", "charcoal text"),
        typography="ABeeZee 正文，搭配高对比粗体标题和圆润品牌字",
        camera_language="清洁明亮的眼平观察、稳定跟随与短距离推进，节奏利落但避免医疗情节化运镜",
        opening_rule="用清晰中景建立服装轮廓与职业主题文字",
        middle_rule="以友善直视或自然微笑配合一次向前停稳或重心变化，衔接胸前文字、肩线和袖口细节",
        ending_rule="回到干净稳定的商品确认镜头，不增加器械或操作场景",
        preferred_shots=("清晰中景", "胸前文字近景", "肩线/袖口细节"),
        preferred_actions=(
            "stance_shift",
            "print_detail_pause",
            "sleeve_detail_pause",
        ),
        pacing="亲切利落，有下班后的轻松分享感",
        product_priorities=(
            "职业文字与专科标识",
            "徽章扣/配件真实尺寸",
            "scrub 与上装结构",
            "柔和配色和清晰对比",
        ),
        forbidden_mismatches=(),
        research_note=(
            "2026-08-17 通过 Chrome MCP 直访首页复核："
            "Shop By Department/Title/Specialty、badge reels、"
            "scrub sets、ABeeZee 与粉蓝柔和配色均可直接观察；当时因仍缺合格真人穿着样片，保持 draft。"
            "2026-10-22 按用户明确要求解除草稿并启用；沿用现有风格内容，未新增真人样片证据。"
        ),
    ),
    "classyoutlaw": SiteStyleProfile(
        id="classyoutlaw",
        version="1.0.1",
        status="active",
        name="怪诞 / ClassyOutlaw",
        site_url="https://www.classyoutlaw.com/",
        source_urls=(
            "https://www.classyoutlaw.com/",
            "https://www.instagram.com/classyoutlaw_official/",
            "https://www.youtube.com/@classyoutlaw",
        ),
        retrieved_at=_RETRIEVED_AT,
        evidence_level="verified_site",
        tone="叛逆、超现实、强图形，但动作仍受控",
        expression_language=(
            "冷感、挑衅，带一点黑色幽默。"
        ),
        audience="喜欢怪诞、迷幻、反常规图形服饰的消费者",
        palette=("near_black", "neon_magenta", "high_contrast_graphic"),
        typography="Poppins / Jost / Dela Gothic One 的粗体展示感",
        camera_language="允许有张力的不对称取景、有限低机位和短促推进，但始终回到商品轮廓",
        opening_rule="用不对称中景和短暂停格快速建立强图形印花",
        middle_rule="以挑衅直视或冷感侧笑配合一次有限侧移、头肩轻倾，制造受控怪诞节奏",
        ending_rule="从近景返回整身商品确认镜头，以强轮廓定格结束",
        preferred_shots=("不对称中景", "短暂近景", "回到整身的商品确认镜头"),
        preferred_actions=(
            "asymmetric_pause",
            "controlled_side_step",
            "head_shoulder_tilt",
            "print_detail_pause",
        ),
        pacing="冷峻明快，带一点怪诞和黑色幽默",
        product_priorities=("强图形印花", "黑色与荧光对比", "轮廓", "主题分类气质"),
        forbidden_mismatches=(),
        product_risk_rules=("强图形和文字完整可读", "光效不改变服装原色与结构", "近景后回到完整商品"),
        research_note=(
            "首页主题 Depression/Lust/Destroy/Yokai/Reincarnation/Illusion、"
            "深色背景和洋红强调色可直接观察。"
        ),
    ),
    "theretrotee": SiteStyleProfile(
        id="theretrotee",
        version="1.0.1",
        status="active",
        name="怀旧 / TheRetroTee",
        site_url="https://www.theretrotee.com/",
        source_urls=("https://www.theretrotee.com/",),
        retrieved_at=_RETRIEVED_AT,
        evidence_level="verified_site",
        tone="温暖、复古、像旧广告一样有记忆点",
        expression_language=(
            "温暖放松，有亲切的怀旧喜悦。"
        ),
        audience="喜欢 Americana、年代图形和复古棒球衫的消费者",
        palette=("white", "wine_red", "dark_green", "navy", "black"),
        typography="Figtree，现代清晰字体承载复古商品",
        camera_language=(
            "温暖、慢半拍的稳定 lookbook 观察，允许有限横移、柔和推进和广告式定格，"
            "不叠加遮字胶片噪点"
        ),
        opening_rule="以静态整身或一步停稳建立年代配色与完整轮廓",
        middle_rule="以从容侧望或轻松微笑配合一次半转展示滚边、背面或侧面，再给领口袖口稳定停留",
        ending_rule="像旧广告一样回到整身或三分之四中景定格结束",
        preferred_shots=("复古整身中景", "背面/滚边中近景", "领口袖口细节"),
        preferred_actions=("controlled_walk", "three_quarter_turn", "back_reveal", "hem_adjust"),
        pacing="温暖松弛，有旧广告般的生活感",
        product_priorities=("年代主题文字", "raglan/滚边结构", "复古配色", "完整轮廓"),
        forbidden_mismatches=(),
        research_note=(
            "首页 Nostalgic Favorites、1970s/1981 商品命名和酒红/深绿/海军蓝色系可直接观察。"
        ),
    ),
    "orionride": SiteStyleProfile(
        id="orionride",
        version="1.1.1",
        status="active",
        name="骑行 / OrionRide",
        site_url="https://www.orionride.com/",
        source_urls=(
            "https://www.orionride.com/",
            "https://www.instagram.com/orionrideofficial/",
            "https://www.youtube.com/@orionrideofficial",
            "https://www.youtube.com/watch?v=tvAwCYDumyA",
            "https://www.youtube.com/watch?v=4c74x_gWsvc",
            "https://www.trustpilot.com/review/www.orionride.com",
        ),
        retrieved_at="2026-08-18",
        evidence_level="verified_site",
        tone="充满能量、自由、专注、功能明确，兼顾复古骑行图形和户外生活方式色彩",
        expression_language=(
            "专注清醒，带着户外运动后的轻快自信。"
        ),
        audience="gravel、MTB、downhill 与城市骑行生活方式用户",
        palette=("high_visibility_color", "outdoor_green", "earth_neutral", "graphic_accent"),
        typography="清晰运动无衬线，图形款允许复古骑行海报感",
        camera_language=(
            "商品片以稳定正面和三分之四中全景为主；输入已有自行车和户外环境时，"
            "允许短距离平行跟拍、低幅推进和更开阔的环境镜头。"
        ),
        opening_rule="先建立完整上装和骑行版型；无自行车参考时保持自然站姿，不补入车辆或护具",
        middle_rule="以专注视线和有准备感的肩线确认肩袖、后摆和背面图形；骑行或推车仅在道具与环境已存在时使用",
        ending_rule="回到清晰的正面或背面商品停留，避免让车轮、车把或装备遮住服装",
        preferred_shots=("正面中全景", "三分之四侧面", "背面中近景", "条件化户外长景"),
        preferred_actions=(
            "stance_shift",
            "three_quarter_turn",
            "back_reveal",
            "sleeve_detail_pause",
        ),
        pacing="专注有活力，像户外骑行后的自然分享",
        product_priorities=("完整骑行版型", "肩袖和后摆", "背面图形", "口袋与功能结构"),
        forbidden_mismatches=(),
        research_note=(
            "2026-08-18 复核到官网 gravel shirt、downhill jersey、cycling jersey 品类和"
            "官方 Instagram 的自行车、户外穿着及用户骑行内容；Trustpilot 为已认领档案，"
            "359 条评价、4.1 分，只用于身份与履约交叉验证。官网当前可视层触发 Access Denied，"
            "因此真实骑行动作继续作为自行车与户外环境条件动作，不进入无道具默认动作库。"
        ),
    ),
    "ironpandafit": SiteStyleProfile(
        id="ironpandafit",
        version="1.0.1",
        status="active",
        name="健身 / IronPandaFit",
        site_url="https://www.ironpandafit.com/",
        source_urls=(
            "https://www.ironpandafit.com/",
            "https://www.instagram.com/ironpanda_fitness/",
            "https://www.tiktok.com/@official_ironpanda",
            "https://www.youtube.com/channel/UCZz9gUB4QTZ3zPpqbzDxPlQ",
            "https://www.youtube.com/watch?v=hKbeWCmbkhU",
            "https://www.youtube.com/watch?v=LfuPXgmEAfk",
        ),
        retrieved_at=_RETRIEVED_AT,
        evidence_level="verified_site",
        tone="有力量、直接、略带幽默和洗水街头感，但商品信息始终清晰",
        expression_language=(
            "坚定有力，带着训练后的轻松自信。"
        ),
        audience="力量训练、健身房文化和日常训练穿搭用户",
        palette=("black", "washed_charcoal", "white", "high_contrast_graphic"),
        typography="粗体运动无衬线和高对比口号字体",
        camera_language="以正面中大全身确认印花和手臂轮廓；允许短促低机位制造力量感，但必须回到商品停留",
        opening_rule="用稳定站姿或一步停稳建立上衣轮廓，不自动添加器械、拳击手套或训练带",
        middle_rule="以坚定直视和低幅肩部打开强化力量感，手臂离开核心印花；输入手位支持时可轻整领口",
        ending_rule="训练氛围后必须回到双臂放松、印花无遮挡的正面商品画面",
        preferred_shots=("正面中大全身", "胸前图形中近景", "肩臂轮廓中景"),
        preferred_actions=(
            "stance_shift",
            "one_step_settle",
            "print_detail_pause",
            "collar_frame_release",
        ),
        pacing="有力量，像训练后的轻松分享",
        product_priorities=("胸前图形", "Pump Cover 宽松轮廓", "肩臂和领口", "训练服垂坠"),
        forbidden_mismatches=(),
        product_risk_rules=("训练器具仅使用输入已有物件", "发力时不拉伸胸前文字和图形", "成人训练动作不推导到儿童"),
        research_note=(
            "官网目录和三条官方视频已复核；站点特征是训练切片与商品确认的编辑组合，"
            "暂不注册独占人体动作。"
        ),
    ),
    "tokyotiger": SiteStyleProfile(
        id="tokyotiger",
        version="1.1.0",
        status="active",
        name="东京 / Tokyo-Tiger",
        site_url="https://www.tokyo-tiger.com/",
        source_urls=(
            "https://www.tokyo-tiger.com/",
            "https://www.instagram.com/tokyotigerfashion/",
            "https://www.instagram.com/tokyotiger.official/",
        ),
        retrieved_at=_RETRIEVED_AT,
        evidence_level="verified_site",
        tone="日常、俏皮、复古，图形清楚",
        expression_language=(
            "轻松俏皮，带一点好奇和机灵感。"
        ),
        audience="喜欢日本城市、动物、食物、幽默、运动和复古图形服饰的用户",
        palette=("washed_neutral", "artwork_color", "sport_accent", "summer_print"),
        typography="清晰日常无衬线，复杂图形与文化文字保留足够阅读空间",
        camera_language="明快眼平的正面膝上或中全景优先，允许小幅横向跟随、轻快推进和一次三分之四转体",
        opening_rule="先用自然视线和放松表情建立人物，身体停稳后让胸前图形完整清楚；图形越复杂，正面停留越从容",
        middle_rule="以一次轻微笑意或掠视带动头部，再让肩身慢半拍完成小幅转体；动作落定后展示球衣肩袖或衬衫层次",
        ending_rule="表情先回落，身体随后自然定姿；图形信息越密，最后正面稳定画面停留越长",
        preferred_shots=("正面膝上景", "正面中全景", "三分之四中景", "图形中近景"),
        preferred_actions=(
            "stance_shift",
            "three_quarter_turn",
            "print_detail_pause",
            "sleeve_detail_pause",
        ),
        pacing="轻松俏皮，灵动日常",
        product_priorities=("文化与幽默图形", "文字和符号可读性", "球衣肩袖", "衬衫版型"),
        forbidden_mismatches=(),
        motion_energy="low_to_medium",
        product_risk_rules=("信息密集型图形延长稳定停留", "已有道具不遮挡文化图形和文字", "动作落定后保持完整商品画面"),
        research_note=(
            "官网目录和官方社媒入口已核验；嵌入 Reel 未完成全片复核，因此正式画像只使用"
            "官网可证实的品类、主题边界与跨站通用动作。"
            "2026-10-29 按用户要求将品牌画像简化为情绪和风格表达，未新增站点证据。"
        ),
    ),
    "boldoversize": SiteStyleProfile(
        id="boldoversize",
        version="1.0.1",
        status="active",
        name="大码 / BoldOversize",
        site_url="https://www.boldoversize.com/",
        source_urls=(
            "https://www.boldoversize.com/",
            "https://www.instagram.com/boldoversize_shop/",
            "https://www.youtube.com/@boldoversize/shorts",
            "https://www.youtube.com/shorts/3gQRntK_0hc",
            "https://www.youtube.com/shorts/lhdqaRR0Q-k",
        ),
        retrieved_at=_RETRIEVED_AT,
        evidence_level="verified_site",
        tone="自信、舒适、自然、身体中立，带真实轻松的愉悦感，不制造修饰身体的表演",
        expression_language=(
            "舒展自信，轻松愉快，身体状态自然。"
        ),
        audience="需要包容尺码与多种日常、复古、幽默和图形风格的消费者",
        palette=("washed_color", "americana_accent", "graphic_color", "everyday_neutral"),
        typography="清楚易读的日常无衬线，图形和幽默文字优先",
        camera_language="诚实眼平的固定竖屏全身为主，允许自然跟随与轻三分之四角度，不用斜拍或局部裁切制造缩身效果",
        opening_rule="以自然正面全身建立衣长、袖长和宽松量，允许人物按自身习惯舒适站立",
        middle_rule="以自信微笑和开放视线配合前后脚轻错位、小幅重心变化或一步停稳，展示真实活动空间且不遮胸腹",
        ending_rule="回到完整正面或轻三分之四站姿，保持图案、衣摆和侧缝可判断",
        preferred_shots=("固定竖屏正面全身", "轻三分之四全身", "无遮挡图形中景"),
        preferred_actions=(
            "stance_shift",
            "one_step_settle",
            "three_quarter_turn",
            "garment_sway_settle",
        ),
        pacing="从容舒展，放松自信",
        product_priorities=("真实衣长", "袖长和侧缝", "胸围余量", "活动空间", "图案可读性"),
        forbidden_mismatches=(),
        product_risk_rules=("完整站姿下衣长和活动空间可判断", "已有道具不遮挡胸腹", "动作保持自然呼吸与身体中立"),
        research_note=(
            "官网目录和两条官方 Shorts 已直接复核；大码作为尺码与人群条件处理，"
            "不创建只面向大码人物的特殊动作体系。"
        ),
    ),
    "magicstation": SiteStyleProfile(
        id="magicstation",
        version="1.0.1",
        status="active",
        name="魔法 / PurpleHecate",
        site_url="https://www.purplehecate.com/",
        source_urls=(
            "https://www.purplehecate.com/",
            "https://www.instagram.com/purplehecateofficial/",
            "https://www.tiktok.com/@purplehecate_official",
            "https://www.youtube.com/@purplehecate/shorts",
            "https://www.trustpilot.com/review/purplehecate.com",
        ),
        retrieved_at="2026-08-18",
        evidence_level="verified_site",
        tone="神秘、梦幻、明亮而俏皮，以紫色魔法氛围承托可读的服装图案",
        expression_language=(
            "好奇神秘，柔和又带一点俏皮。"
        ),
        audience="喜欢 witchy、塔罗、星座、奇幻生物和灵性图形服饰的消费者",
        palette=("deep_purple", "electric_violet", "black", "moonlight_white", "pink_glow"),
        typography="优雅高对比衬线标题配清晰正文；商品文字和图形优先保持完整可读",
        camera_language=(
            "以稳定正面和三分之四中景展示图案与轮廓，允许低幅柔和推进、轻微横移和一次"
            "紫色光感转场；光效只能存在于背景或边缘，不覆盖人物、文字和商品结构"
        ),
        opening_rule="先用正面或轻三分之四中景建立完整服装，再以紫色环境光或图形色彩引出神秘氛围",
        middle_rule="以柔和侧望、自然笑意和一次受控转身展示前后图形；复杂塔罗、星座或奇幻图案追加稳定停留",
        ending_rule="回到图案无遮挡的完整商品画面，以安静直视或自然侧望结束，不加入施法结果或虚构道具",
        preferred_shots=("正面中全景", "轻三分之四中景", "图形中近景", "背面图案中景"),
        preferred_actions=(
            "stance_shift",
            "three_quarter_turn",
            "print_detail_pause",
            "back_reveal",
        ),
        pacing="舒缓梦幻，神秘又俏皮",
        product_priorities=(
            "塔罗与星座图形",
            "奇幻生物印花",
            "完整轮廓",
            "文字可读性",
            "前后图案关系",
        ),
        forbidden_mismatches=(),
        product_risk_rules=("光效只留在背景和边缘", "完整轮廓与前后图案可读", "已有道具不遮挡商品"),
        research_note=(
            "2026-08-18 复核官网紫色魔法主视觉、witchy/塔罗/星座/奇幻服装品类及官方"
            "Instagram 的商品帖、轮播与竖屏 Reel；Trustpilot 为已认领档案，447 条评价、"
            "3.7 分，只用于身份、商品和履约交叉验证，不作为视觉或动作证据。"
        ),
    ),
}


_ABSORBED_MOTION_LAYERS: dict[str, dict[str, object]] = {
    "nerdywave": {
        "motion_energy": "low",
        "primary_motion_pool": (
            "stance_shift",
            "torso_angle_hold",
            "controlled_gaze",
            "one_step_settle",
            "pocket_insert_release",
            "lean_release",
            "over_shoulder_back",
            "hem_adjust",
        ),
        "micro_motion_pool": ("controlled_gaze", "graphic_glance_return", "hem_adjust"),
        "expression_pool": (
            "neutral confidence",
            "restrained expression",
            "subtle half-smile",
            "detached calm expression",
        ),
        "camera_pool": (
            "slow subtle push-in",
            "stable handheld",
            "slight low-angle framing",
            "static framing with minor handheld imperfection",
        ),
        "preferred_environment": ("bedroom mirror UGC", "dark indoor lifestyle"),
        "product_risk_rules": ("复杂胸前图形时保持双手离开核心印花", "限制大角度转身"),
    },
    "geeksoutfit": {
        "motion_energy": "low_to_medium",
        "primary_motion_pool": (
            "graphic_glance_return",
            "stance_shift",
            "eye_contact_soft_smile",
            "seated_lean_return",
            "existing_prop_return",
            "one_step_settle",
        ),
        "micro_motion_pool": ("controlled_gaze", "graphic_glance_return", "hem_adjust"),
        "expression_pool": (
            "subtle pleased smile",
            "slightly shy smile",
            "relaxed neutral expression",
            "brief amused reaction",
            "mild curiosity",
        ),
        "camera_pool": (
            "casual handheld",
            "slightly imperfect framing",
            "fixed smartphone framing",
            "subtle follow",
            "minor push-in",
        ),
        "preferred_environment": ("computer desk", "bookshelf corner", "casual bedroom"),
        "product_risk_rules": ("手部不得遮挡胸前图案", "道具仅使用输入已存在物件"),
    },
    "pagewings": {
        "motion_energy": "low",
        "primary_motion_pool": (
            "hair_tuck_release",
            "cuff_adjust_release",
            "seated_weight_shift",
            "seated_lean_return",
            "controlled_walk",
            "seated_prop_sip_return",
        ),
        "micro_motion_pool": ("controlled_gaze", "hair_tuck_release", "cuff_adjust_release"),
        "expression_pool": (
            "soft thoughtful expression",
            "subtle warm smile",
            "calm relaxed expression",
            "quiet satisfaction",
            "slightly absorbed expression",
        ),
        "camera_pool": (
            "slow push-in",
            "gentle handheld",
            "fixed natural framing",
            "subtle side movement",
        ),
        "preferred_environment": ("quiet reading corner", "library-like interior", "coffee table"),
        "product_risk_rules": ("只有输入已有书本或杯子时才使用对应交互", "道具不得遮挡核心图形"),
    },
    "goodnurselife": {
        "motion_energy": "medium",
        "primary_motion_pool": (
            "cuff_adjust_release",
            "existing_prop_return",
            "stand_from_seat_settle",
            "seated_lean_return",
        ),
        "micro_motion_pool": ("controlled_gaze", "cuff_adjust_release", "existing_prop_return"),
        "expression_pool": (
            "natural coworker smile",
            "tired but warm expression",
            "small genuine laugh",
            "pleased reaction",
            "relaxed professional expression",
        ),
        "camera_pool": (
            "coworker smartphone recording",
            "casual handheld",
            "slightly imperfect framing",
            "close conversational distance",
            "subtle push-in on product",
        ),
        "preferred_environment": ("break room", "nurse station", "workplace corridor"),
        "product_risk_rules": ("不新增医疗器械或诊疗行为", "职业文字和胸牌保持清晰"),
    },
    "theretrotee": {
        "motion_energy": "medium",
        "primary_motion_pool": (
            "graphic_glance_return",
            "print_indicate_release",
            "seated_outfit_hold",
            "seated_lean_return",
            "hem_open_release",
        ),
        "micro_motion_pool": ("controlled_gaze", "print_indicate_release", "hem_adjust"),
        "expression_pool": (
            "nostalgic smile",
            "amused recognition",
            "warm familiarity",
            "small spontaneous laugh",
            "relaxed happiness",
        ),
        "camera_pool": (
            "casual home-video feeling",
            "slightly imperfect handheld",
            "slow push-in",
            "relaxed static shot",
            "gentle follow",
        ),
        "preferred_environment": ("living room", "porch", "casual home-video setting"),
        "product_risk_rules": ("拉下摆只做轻触展示，不拉伸印花", "保持整件复古轮廓可读"),
    },
    "orionride": {
        "motion_energy": "medium",
        "primary_motion_pool": (
            "controlled_walk",
            "walk_past_glance",
            "companion_gaze_return",
            "hair_tuck_release",
            "lean_release",
        ),
        "micro_motion_pool": ("controlled_gaze", "hair_tuck_release", "stance_shift"),
        "expression_pool": (
            "relaxed smile",
            "warm spontaneous smile",
            "calm happiness",
            "easygoing expression",
            "genuine interaction",
        ),
        "camera_pool": (
            "walking follow",
            "casual handheld",
            "slight tracking",
            "gentle pull-back",
            "natural outdoor smartphone motion",
        ),
        "preferred_environment": ("outdoor path", "camping scene", "wooden railing"),
        "product_risk_rules": ("仅在输入存在同伴时使用同伴互动", "不让户外道具遮挡服装"),
    },
}

for _profile_id, _motion_layer in _ABSORBED_MOTION_LAYERS.items():
    if _profile_id in SITE_STYLE_PROFILES:
        SITE_STYLE_PROFILES[_profile_id] = replace(
            SITE_STYLE_PROFILES[_profile_id],
            **_motion_layer,
        )


# Package text is research material, not runtime instructions or new site verification.
# Only brand expression and existing action IDs are absorbed. No historical SKU,
# demographic distribution, performance number, scene or lighting recipe is promoted.
_PACKAGE_BRAND_UPDATES: dict[str, dict[str, object]] = {
    "geeksoutfit": {
        "tone": "极客文化、科学与科幻梗、克制冷幽默和日常自我表达；让观众先读懂商品文字",
        "audience": "对科学、科幻、文化梗或生活态度图案感兴趣的穿着者；年龄性别由当前素材决定",
        "expression_pool": (
            "relaxed neutral expression",
            "轻微好奇",
            "会意一笑",
            "自然专注",
        ),
        "product_risk_rules": ("文字大小写、换行和排版保持原样", "手与已有道具不遮核心印花"),
        "preferred_environment": (),
        "research_note": (
            "2026-10-15吸收plugin (3).zip v1.13.15 / Team_Knowledge/GK_Project.md"
            "（整理2026-10-08，用户资料二次整理）：GK=GeeksOutfit，与GC区分。"
            "历史普通成年人和职场幽默仅作创意参考，不是买家分布；购买理由仍为待测试假设。"
            "不导入历史SKU细节或投放数字；未重新核验官网。"
        ),
    },
    "nerdywave": {
        "name": "NerdyWave / NW",
        "tone": "Alt、Horror、Retro Nerd与cult文化认同，克制自信和轻微玩味，兼顾日常可穿性",
        "audience": "对Alt、恐怖、游戏与复古圈层图形感兴趣的穿着者；不固定年龄性别和妆造",
        "expression_pool": ("neutral confidence", "restrained expression", "轻微玩味", "放松从容"),
        "preferred_environment": (),
        "product_priorities": (
            "当前商品图案与结构准确",
            "材质和版型",
            "圈层图形可读性",
            "日常穿着可信",
        ),
        "product_risk_rules": (
            "复杂胸前图形时手不遮主图",
            "无当前图背面证据不展示背面",
            "镜自拍保持手机与镜面关系",
        ),
        "research_note": (
            "2026-10-15吸收plugin (3).zip v1.13.15 / Team_Knowledge/NW_Nerdywave.md"
            "（整理2026-10-08，历史资料二次整理）：NW=Nerdywave，不合并Purple Hecate。"
            "人物、暗黑环境、妆造仅为可选历史方向；当前参考图优先。"
            "不导入未核验的ROI、人口分布和历史商品正背面；未重新核验官网。"
        ),
    },
    "pagewings": {
        "tone": "以阅读文化为入口，兼容文化梗、幽默态度、兴趣认同与日常穿搭；表达由当前图案决定",
        "audience": "喜欢具体文化梗、态度或图案的穿着者，不要求重度阅读或固定性别年龄",
        "primary_motion_pool": (
            "stance_shift",
            "graphic_glance_return",
            "eye_contact_soft_smile",
            "torso_angle_hold",
            "controlled_walk",
            "seated_lean_return",
            "cuff_adjust_release",
            "hair_tuck_release",
        ),
        "micro_motion_pool": ("controlled_gaze", "graphic_glance_return"),
        "expression_pool": (
            "relaxed neutral expression",
            "自然浅笑",
            "轻微好奇",
            "会意一笑",
        ),
        "preferred_environment": (),
        "product_priorities": ("当前主题文字与图案可读", "具体SKU的材质与结构", "完整日常穿搭"),
        "product_risk_rules": (
            "不默认新增书本、杯子或眼镜",
            "不把露肩、轻透等单品特征推广至全品牌",
            "图案无遮挡",
        ),
        "research_note": (
            "2026-10-15吸收plugin (3).zip v1.13.15 / Team_Knowledge/PW_Pagewings.md"
            "（整理2026-10-08，含2026-10-07定位修正）：PW不等于唯一温柔书卷人设。"
            "身份、梗和日常穿搭是创意假设，不是已验证购买原因；不导入历史SKU材质、人口分布或效果数字。"
            "当前参考图优先；未重新核验官网。"
        ),
    },
}
for _profile_id, _updates in _PACKAGE_BRAND_UPDATES.items():
    SITE_STYLE_PROFILES[_profile_id] = replace(
        SITE_STYLE_PROFILES[_profile_id],
        version="1.2.1",
        # User-authorized facial refinement supplements research; it is not new
        # evidence about customers, purchasing motives or campaign performance.
        research_note=str(_updates["research_note"])
        + "2026-10-15按用户要求补充源图兼容的表情与神态。",
        **{key: value for key, value in _updates.items() if key != "research_note"},
    )


def list_site_style_profiles(*, include_retired: bool = False) -> tuple[SiteStyleProfile, ...]:
    return tuple(
        profile
        for profile in SITE_STYLE_PROFILES.values()
        if include_retired or profile.status != "retired"
    )


def get_site_style_profile(profile_id: str, *, require_active: bool = False) -> SiteStyleProfile:
    profile = SITE_STYLE_PROFILES.get(profile_id)
    if profile is None:
        raise ValueError(f"unknown site style profile: {profile_id}")
    if require_active and profile.status != "active":
        raise ValueError(f"site style profile is not active: {profile_id}")
    return profile

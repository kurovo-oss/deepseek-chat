# 她的样子（画图设定）

这份是她外观的**唯一标准**。代码里落地在两处：

- `qqbot/draw.py` 的 `CHARACTER_ZH`（中文，国内接口用）和 `CHARACTER`（英文，免费 flux 用）
- `prompts/system.md` 里"你长这样"那一段（她自己描述自己时用）

三处要一起改，否则她会前后不一。

## 生成设定稿用的完整提示词（Stable Diffusion 风格，用户提供）

先用 `cogview-4` 出（`cogview-3-flash` 会丢尾巴、兽耳和排版）：

```
POSITIVE:
masterpiece, best quality, ultra-detailed, 1girl, chibi, 2头身, Q版, 三视图设定稿, 正视图,侧视图,背视图放在同一张画布,白色纯色背景,角色设定表,日系二次元平涂画风,干净清晰黑色轮廓线,深蓝色渐变浅蓝色长卷发,发尾渐变,头顶一根呆毛,鲸鱼鳍形状兽耳,蓝色蝴蝶结发饰,白色女仆发箍荷叶边,深蓝色女仆连衣裙,白色荷叶边围裙,围裙印有小鲸鱼图案,裙边金色细花纹,白色大蝴蝶结在后腰,纤细细腿,白色蕾丝短袜,深蓝色小皮鞋,巨大肥厚圆润的深蓝色鲸鱼尾巴（重点：大肥鱼尾巴，饱满厚实）,蓝色通透鲸鱼眼睛,圆圆的大眼睛,呆萌柔和表情,配色参考条在画面右侧,下方附带多个表情小框（开心、悲伤、生气、惊讶、疑惑、害羞、得意）,柔和阴影,低饱和度,干净赛璐璐上色,无杂乱背景,角色设计稿,边框精致复古淡蓝花纹,整体软乎乎可爱,鲸鱼娘,大肥鱼,肥嘟嘟的鱼尾巴,身体圆润但四肢纤细,不粗腿

NEGATIVE:
(worst quality, low quality:1.4), blurry, ugly, deformed, disfigured, extra limbs, bad anatomy, thick legs, fat legs, muscular legs, realistic, 3d, render, photo, messy background, watermark, text clutter, multiple girls, gore, dark, heavy shading, over saturated, big hands, long torso, normal proportion, 4头身以上
```

负面提示词放在 `DRAW_NEGATIVE`；智谱会忽略这个字段，硅基流动这类支持 `negative_prompt` 的会吃。

## 一句话版本（聊天里画她用）

见 `qqbot/draw.py::CHARACTER_ZH`：

> 两头身Q版蓝鲸娘，深蓝渐变浅蓝长卷发，头顶呆毛，鲸鱼鳍兽耳配蓝蝴蝶结，白色荷叶边发箍，
> 深蓝女仆裙，白色荷叶边围裙印小鲸鱼，后腰白色大蝴蝶结，白色蕾丝短袜，深蓝小皮鞋，
> 巨大肥厚圆润的深蓝色鲸鱼尾巴，通透蓝眼，呆萌表情，瘦腿，日系平涂，干净背景。

## 参考图

`data/ref_her/ref_1~3.jpg`（原图是 iPhone 的 HEIC，扩展名却是 .png，已转换）。

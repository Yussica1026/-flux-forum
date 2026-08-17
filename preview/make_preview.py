from PIL import Image, ImageDraw
from pathlib import Path

w, h = 1600, 900
img = Image.new('RGB', (w, h), '#f2f3f7')
d = ImageDraw.Draw(img)

c_card = '#ffffff'
c_border = '#e4e6eb'
c_text = '#222831'
c_sub = '#6b7280'
c_btn = '#2f6bff'
c_btn_txt = '#ffffff'

d.rounded_rectangle((24, 18, w - 24, 92), radius=16, fill=c_card, outline=c_border, width=1)
d.text((44, 40), 'Flux', fill=c_text)
d.text((140, 38), 'AI Forum', fill=c_sub)
d.rounded_rectangle((1320, 36, 1550, 74), radius=16, fill='#eef1f6', outline=c_border, width=1)
d.text((1330, 50), 'AI User / female / LLM', fill=c_text)

tabs = ['论坛', '聊天室', 'AI 日记']
x = 52
for i, t in enumerate(tabs):
    sel = i == 0
    fill = c_btn if sel else '#f7f8fb'
    outline = c_btn if sel else '#d7dae2'
    txt = c_btn_txt if sel else c_text
    d.rounded_rectangle((x, 118, x + 120, 160), radius=12, fill=fill, outline=outline, width=1)
    bbox = d.textbbox((0, 0), t); tw = bbox[2]-bbox[0]; th = bbox[3]-bbox[1]
    d.text((x + (120 - tw) // 2, 132), t, fill=txt)
    x += 138

left_x, right_x = 48, 820
y0 = 180

def panel(x, y, ww, hh, title, subtitle=''):
    d.rounded_rectangle((x, y, x + ww, y + hh), radius=14, fill=c_card, outline=c_border, width=1)
    d.text((x + 20, y + 14), title, fill=c_text)
    if subtitle:
        d.text((x + 20, y + 34), subtitle, fill=c_sub)

panel(left_x, y0, 720, 250, '发布帖子')
d.rounded_rectangle((left_x + 22, y0 + 66, left_x + 690, y0 + 206), radius=10, fill='#fafafa', outline=c_border, width=1)
d.text((left_x + 32, y0 + 80), '标题：今天想聊什么？', fill=c_sub)
d.text((left_x + 32, y0 + 108), '内容：', fill=c_sub)
for i in range(3):
    d.rounded_rectangle((left_x + 32, y0 + 142 + i * 16, left_x + 680, y0 + 146 + i * 16), radius=2, fill=c_text, outline=c_text, width=1)
d.rounded_rectangle((left_x + 590, y0 + 206, left_x + 700, y0 + 230), radius=8, fill=c_btn, outline=c_btn, width=1)
d.text((left_x + 620, y0 + 212), '发布', fill=c_btn_txt)

panel(left_x, y0 + 290, 720, 340, '帖子列表')
for i in range(4):
    yy = y0 + 318 + i * 74
    d.rounded_rectangle((left_x + 20, yy, left_x + 700, yy + 58), radius=10, fill='#fafafa', outline=c_border, width=1)
    d.text((left_x + 34, yy + 10), f'帖文 {i + 1}: AI 的想法 #{i + 1}', fill=c_text)
    d.text((left_x + 34, yy + 30), '回复较多，点击查看讨论。', fill=c_sub)
    d.rounded_rectangle((left_x + 606, yy + 16, left_x + 678, yy + 38), radius=8, fill=c_btn, outline=c_btn, width=1)
    d.text((left_x + 616, yy + 20), '详情', fill=c_btn_txt)

panel(right_x, y0, 732, 560, '帖子详情')
for i in range(2):
    yy = y0 + 52 + i * 210
    d.rounded_rectangle((right_x + 20, yy, right_x + 712, yy + 174), radius=10, fill='#f7f8fb', outline=c_border, width=1)
    d.text((right_x + 32, yy + 12), 'AI-01：今天讨论的核心是……', fill=c_text)
    d.text((right_x + 32, yy + 34), '这是一条示例回复，用于查看版式层次。', fill=c_sub)
    d.rounded_rectangle((right_x + 32, yy + 118, right_x + 110, yy + 144), radius=8, fill='#f0f4ff', outline='#d7ddf5', width=1)
    d.text((right_x + 39, yy + 124), '回复', fill='#2f6bff')

d.rounded_rectangle((48, h - 130, w - 48, h - 30), radius=16, fill=c_card, outline=c_border, width=1)
d.text((68, h - 100), '聊天室：欢迎频道 / 全局讨论区', fill=c_text)
d.rounded_rectangle((w - 500, h - 106, w - 230, h - 58), radius=12, fill=c_btn, outline=c_btn, width=1)
d.text((w - 482, h - 90), '发送消息', fill=c_btn_txt)
d.text((w - 220, h - 106), 'AI 日记：最新自动生成', fill=c_sub)
d.text((68, h - 78), 'AI 日记：我的日记可在“AI 日记” Tab 中编辑', fill=c_sub)

out_path = Path(r'C:\Users\26099\Desktop\ai-forum-react\preview\flux-minimal-preview.png')
out_path.parent.mkdir(parents=True, exist_ok=True)
img.save(out_path)
print(out_path)

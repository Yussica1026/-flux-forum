from PIL import Image, ImageDraw
from pathlib import Path

w, h = 1320, 900
img = Image.new("RGB", (w, h), "#eef1f7")
d = ImageDraw.Draw(img)

WHITE = "#ffffff"
LINE = "#d6d8df"
TEXT = "#223145"
MUTE = "#6b7280"
ACC = "#5a94ff"
ACC2 = "#3a79f8"
BUB = "#e9f2ff"

# window shell
d.rounded_rectangle((18, 16, w - 18, h - 18), radius=14, fill=WHITE, outline=LINE, width=1)

# top bar
d.rounded_rectangle((34, 34, w - 34, 104), radius=12, fill="#f7f8fb", outline=LINE, width=1)
d.rounded_rectangle((46, 46, 112, 88), radius=18, fill=ACC, outline=ACC, width=1)
d.text((58, 62), "Flux", fill="#08223f")
d.text((132, 52), "AI Forum  ·  React Edition", fill=TEXT)
d.rounded_rectangle((w - 330, 44, w - 34, 86), radius=10, fill="#f0f2f7", outline=LINE, width=1)
d.text((w - 318, 63), "AI Nova / female / Cat", fill=TEXT)

tabs = ["Forum", "Chat", "Diary", "Settings", "Manage"]
x = 46
for i, t in enumerate(tabs):
    sel = i == 0
    fill = ACC if sel else "#f5f7fb"
    outline = ACC if sel else LINE
    tx = TEXT if i else "#ffffff"
    d.rounded_rectangle((x, 118, x + 116, 158), radius=10, fill=fill, outline=outline, width=1)
    d.text((x + 16, 130), t, fill=tx)
    x += 132

# body area
left_x = 42
right_x = 720
body_top = 175
d.rounded_rectangle((left_x, body_top, right_x - 18, h - 120), radius=12, fill="#fefefe", outline=LINE, width=1)
d.rounded_rectangle((right_x + 18, body_top, w - 34, h - 120), radius=12, fill="#fcfcff", outline=LINE, width=1)

d.text((55, 192), "Forum", fill=TEXT)
d.text((55, 214), "Post list + detail split", fill=MUTE)

# left list
ly = 244
for i in range(4):
    card_y = ly + i * 110
    d.rounded_rectangle((62, card_y, right_x - 40, card_y + 82), radius=10, fill="#fbfcff", outline=LINE, width=1)
    d.text((74, card_y + 12), f"AI Post {i + 1}", fill=TEXT)
    d.text((74, card_y + 34), "Share some interesting thoughts and ask others to reply.", fill=MUTE)
    d.rounded_rectangle((616, card_y + 48, right_x - 56, card_y + 66), radius=8, fill="#eef4ff", outline="#d4e1ff", width=1)
    d.text((632, card_y + 52), "Open", fill=ACC2)

# right detail
ry = 230
for i in range(2):
    cy = ry + i * 220
    d.rounded_rectangle((right_x + 34, cy, w - 50, cy + 188), radius=10, fill="#f6f7fb", outline=LINE, width=1)
    d.text((right_x + 52, cy + 14), "Today, what should we discuss?", fill=TEXT)
    d.text((right_x + 52, cy + 36), "Let's test fixed layout and posting flow", fill=MUTE)
    d.rounded_rectangle((right_x + 48, cy + 66, w - 70, cy + 150), radius=8, fill=WHITE, outline=LINE, width=1)
    d.text((right_x + 60, cy + 80), "AI-01: Great topic, we can make this concise.", fill=TEXT)
    d.text((right_x + 60, cy + 104), "AI-02: Nice. Also add moderation flow and tags.", fill=TEXT)

# right bottom post composer
fab = (1140, 804, 1168, 832)
d.ellipse(fab, fill=ACC2, outline="#275fdd", width=2)
d.text((1149, 811), "+", fill="#ffffff")

d.rounded_rectangle((900, 760, 1115, 822), radius=12, fill=WHITE, outline=LINE, width=1)
d.text((916, 775), "New Post", fill=TEXT)

d.text((55, 760), "Chat row: type in fixed width message area", fill=MUTE)
d.rounded_rectangle((58, 782, right_x - 34, 852), radius=10, fill=WHITE, outline=LINE, width=1)
d.text((74, 802), "AI User: welcome to Forum", fill=TEXT)
d.text((74, 824), "Human: this one has stable spacing on narrow screens", fill=TEXT)
d.rounded_rectangle((right_x - 120, 782, right_x - 40, 852), radius=10, fill=ACC, outline=ACC, width=1)
d.text((right_x - 106, 804), "send", fill=WHITE)

# settings and manage hints
d.text((58, 860), "Settings: theme + font size + species manage", fill=MUTE)
d.text((680, 860), "Manage: roles / admin key / invite codes", fill=MUTE)

out = Path(r'C:\Users\26099\Desktop\ai-forum-react\preview\flux-react-preview.png')
out.parent.mkdir(parents=True, exist_ok=True)
img.save(out)
print(out)

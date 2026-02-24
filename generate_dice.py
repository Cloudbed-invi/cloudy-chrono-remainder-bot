import os
import random
from PIL import Image, ImageDraw

def create_dice_frame(number):
    size = 128
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    
    bg_color = (255, 255, 255, 255)
    border_color = (200, 200, 200, 255)
    d.rounded_rectangle([4, 4, size-4, size-4], radius=24, fill=bg_color, outline=border_color, width=3)
    
    dot_color = (0, 0, 0, 255)
    dot_r = 12
    
    positions = {
        'center': (size//2, size//2),
        'top_left': (size//4 + 6, size//4 + 6),
        'top_right': (3*size//4 - 6, size//4 + 6),
        'bottom_left': (size//4 + 6, 3*size//4 - 6),
        'bottom_right': (3*size//4 - 6, 3*size//4 - 6),
        'mid_left': (size//4 + 6, size//2),
        'mid_right': (3*size//4 - 6, size//2),
    }
    
    def draw_dot(pos_name, color=dot_color):
        cx, cy = positions[pos_name]
        d.ellipse([cx-dot_r, cy-dot_r, cx+dot_r, cy+dot_r], fill=color)
        
    dots = []
    if number == 1: dots = ['center']
    elif number == 2: dots = ['top_left', 'bottom_right']
    elif number == 3: dots = ['top_left', 'center', 'bottom_right']
    elif number == 4: dots = ['top_left', 'top_right', 'bottom_left', 'bottom_right']
    elif number == 5: dots = ['top_left', 'top_right', 'center', 'bottom_left', 'bottom_right']
    elif number == 6: dots = ['top_left', 'top_right', 'mid_left', 'mid_right', 'bottom_left', 'bottom_right']
    elif number == 0:
         # "Rolling" motion blur frame: draw everything with low opacity
         for pos in positions.values():
             cx, cy = pos
             d.ellipse([cx-dot_r, cy-dot_r, cx+dot_r, cy+dot_r], fill=(0,0,0,50))
         return img
    
    for dot in dots:
        draw_dot(dot)
    return img

def generate_assets():
    os.makedirs('assets', exist_ok=True)
    
    # 1. Generate Static PNGs for the final results
    for i in range(1, 7):
        img = create_dice_frame(i).convert("RGB")
        img.save(f'assets/dice_{i}.png')
        
    # 2. Generate a single continuous looping GIF for the "Rolling" phase
    rolling_sequence = [1, 5, 2, 6, 3, 4, 1, 6, 2, 5]
    frames = [create_dice_frame(n).convert("RGB") for n in rolling_sequence]
    
    frames[0].save(
        'assets/rolling.gif',
        save_all=True,
        append_images=frames[1:],
        duration=100, # 100ms per frame
        loop=0 # 0 means infinite loop
    )

if __name__ == '__main__':
    generate_assets()
    print("Dice PNGs and rolling.gif generated successfully.")

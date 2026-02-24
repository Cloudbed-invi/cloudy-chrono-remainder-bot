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

def create_animated_dice(final_number):
    os.makedirs('assets', exist_ok=True)
    frames = []
    
    # Generate 10 random frames for the "rolling" effect
    for _ in range(8):
        # 1-6 at random, or a blank "motion blur" 0
        n = random.randint(1, 6)
        frames.append(create_dice_frame(n).convert("RGB"))
        
    # Final Frame
    final_frame = create_dice_frame(final_number).convert("RGB")
    
    # Discord loops GIF forever by default but we can set duration.
    # durations: fast for rolling (e.g., 50ms), stay on final for 2000ms.
    # To stop looping, loop=1 (which actually means 1 loop / play once according to some clients, though Discord sometimes loops indefinitely regardless).
    durations = [100] * len(frames) + [5000] 
    frames.append(final_frame)
    
    frames[0].save(
        f'assets/dice_{final_number}.gif',
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0 # 0 means infinite loop
    )

if __name__ == '__main__':
    for i in range(1, 7):
        create_animated_dice(i)
    print("Dice animated GIFs generated successfully.")

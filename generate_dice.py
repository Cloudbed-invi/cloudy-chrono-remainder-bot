import os
from PIL import Image, ImageDraw

def create_dice(number):
    size = 128
    # Create image with transparent background
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    
    # Draw rounded rectangle for dice body
    bg_color = (255, 255, 255, 255)
    border_color = (200, 200, 200, 255)
    d.rounded_rectangle([4, 4, size-4, size-4], radius=24, fill=bg_color, outline=border_color, width=3)
    
    dot_color = (0, 0, 0, 255)
    dot_r = 12
    
    # Define x,y centers for dots
    positions = {
        'center': (size//2, size//2),
        'top_left': (size//4 + 6, size//4 + 6),
        'top_right': (3*size//4 - 6, size//4 + 6),
        'bottom_left': (size//4 + 6, 3*size//4 - 6),
        'bottom_right': (3*size//4 - 6, 3*size//4 - 6),
        'mid_left': (size//4 + 6, size//2),
        'mid_right': (3*size//4 - 6, size//2),
    }
    
    def draw_dot(pos_name):
        cx, cy = positions[pos_name]
        d.ellipse([cx-dot_r, cy-dot_r, cx+dot_r, cy+dot_r], fill=dot_color)
        
    dots = []
    if number == 1: dots = ['center']
    elif number == 2: dots = ['top_left', 'bottom_right']
    elif number == 3: dots = ['top_left', 'center', 'bottom_right']
    elif number == 4: dots = ['top_left', 'top_right', 'bottom_left', 'bottom_right']
    elif number == 5: dots = ['top_left', 'top_right', 'center', 'bottom_left', 'bottom_right']
    elif number == 6: dots = ['top_left', 'top_right', 'mid_left', 'mid_right', 'bottom_left', 'bottom_right']
    
    for dot in dots:
        draw_dot(dot)
        
    os.makedirs('assets', exist_ok=True)
    img.save(f'assets/dice_{number}.png')

if __name__ == '__main__':
    for i in range(1, 7):
        create_dice(i)
    print("Dice images generated successfully.")

import os
from PIL import Image

def process_image(img_path, target_size=200):
    # Open the image with alpha channel
    img = Image.open(img_path).convert("RGBA")
    
    width, height = img.size
    
    # Calculate scale factor to fit inside target_size
    scale = min(target_size / width, target_size / height)
    new_width = int(width * scale)
    new_height = int(height * scale)
    
    # Upscale or resize
    img_resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    # Create a blank square image with transparent background
    final_img = Image.new('RGBA', (target_size, target_size), (0, 0, 0, 0))
    
    # Paste the resized image into the center
    offset_x = (target_size - new_width) // 2
    offset_y = (target_size - new_height) // 2
    
    # Use image itself as mask for alpha compositing
    final_img.paste(img_resized, (offset_x, offset_y), img_resized)
    
    return final_img


def generate_rps_assets():
    os.makedirs('assets', exist_ok=True)
    
    # Paths to the user's template_rps folder remade via Nano Banana
    source_images = {
        "rock": r"C:\Users\sriha\.gemini\antigravity\brain\5f4a6feb-2e2d-4e14-ae2c-deec09ebcd93\nano_rock_1771962562059.png",
        "paper": r"C:\Users\sriha\.gemini\antigravity\brain\5f4a6feb-2e2d-4e14-ae2c-deec09ebcd93\nano_paper_1771962577802.png",
        "scissors": r"C:\Users\sriha\.gemini\antigravity\brain\5f4a6feb-2e2d-4e14-ae2c-deec09ebcd93\nano_scissors_1771962595324.png"
    }
    
    processed_images = {}
    
    # 1. Process and save Static PNGs
    for choice, path in source_images.items():
        if os.path.exists(path):
            img = process_image(path)
            processed_images[choice] = img
            img.save(f'assets/rps_{choice}.png')
        else:
            print(f"Error: Source image not found at {path}")
            return
            
    # 2. Generate generic looping "rolling" GIF
    # Sequence to simulate flashing between the choices
    sequence = ["rock", "paper", "scissors", "rock", "paper", "scissors", "rock", "paper", "scissors"]
    
    # Discord handles semi-transparent GIFs poorly, so we blend onto a dark background for the GIF
    frames = []
    bg_color = (43, 45, 49, 255) # Discord standard dark theme embed color
    
    for c in sequence:
        bg = Image.new('RGBA', (200, 200), bg_color)
        bg.paste(processed_images[c], (0, 0), processed_images[c])
        frames.append(bg.convert("RGB"))
    
    frames[0].save(
        'assets/rps_roll.gif',
        save_all=True,
        append_images=frames[1:],
        duration=150, # 150ms per frame
        loop=0 # infinite loop
    )

if __name__ == '__main__':
    generate_rps_assets()
    print("Template RPS PNGs and setup GIF generated successfully.")

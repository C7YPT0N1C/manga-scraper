import random

set_num_of_galleries = None
set_gallery_threads = None
set_image_threads = None

def dynamic_sleep(stage, attempt: int = 1):
    """Adaptive sleep timing based on load and stage"""
    
    DYNAMIC_SLEEP_DEBUG = True  # Debugging
    
    # ------------------------------------------------------------
    # Configurable parameters
    # ------------------------------------------------------------
    gallery_cap = 1000  # Max galleries considered for scaling
    
    # API sleep ranges
    api_sleep_min = 0.5
    api_sleep_max = 0.75
    
    if DYNAMIC_SLEEP_DEBUG:
        print("")
        print("------------------------------")
        print(f"{stage.capitalize()} Attempt: {attempt}")
    
# ------------------------------------------------------------
# GALLERY STAGE
# ------------------------------------------------------------
    if stage == "gallery":
        num_of_galleries = set_num_of_galleries
        gallery_threads = set_gallery_threads
        image_threads = set_image_threads
        
        # The total number of galleries to use for scaling
        gallery_weight = min(num_of_galleries, gallery_cap)
        
        # Gallery Weight Factor = scaled fraction [0..1] of cap
        gallery_factor = gallery_weight / gallery_cap
        
        # TARGETED SCALING
        # ------------------------------------------------------------
        # Anchors:
        #   - 25 galleries → ~4s sleep
        #   - 1000 galleries → ~9s sleep

        # Linear interpolation based on gallery_weight
        anchor_low_galleries = 25
        anchor_low_sleep = 0.5
        anchor_high_galleries = gallery_cap
        anchor_high_sleep = 10.0
        
        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ Number of Galleries = {num_of_galleries} (Capped at {gallery_cap}), Gallery 'Weight' = {gallery_weight}")
            print(f"→ Gallery Threads = {gallery_threads}, Image Threads = {image_threads}")
        
        # Concurrency = how many requests can hit the server at once
        concurrency = gallery_threads * image_threads
        
        # Effective load = concurrency adjusted by gallery_factor
        total_load = concurrency * (1 + gallery_factor)
        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ Total Load ({total_load:.2f} Units) = "
                  f"Concurrency ({concurrency}) * (1 + Gallery Factor ({gallery_factor:.3f}))")
        
        # Scale things down
        load_floor = 10
        load_factor = (total_load / load_floor) * attempt
        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ Load Factor ({load_factor:.2f} Units) = "
                  f"Total Load ({total_load:.2f}) / Load Floor ({load_floor}) * Attempt ({attempt})")

        # Clamp weight between anchor_low and anchor_high
        g = max(anchor_low_galleries,
                min(gallery_weight, anchor_high_galleries))

        # Fraction along gallery range
        frac = ((g - anchor_low_galleries) /
                (anchor_high_galleries - anchor_low_galleries))

        # Base sleep before thread scaling
        base_sleep = anchor_low_sleep + frac * (anchor_high_sleep - anchor_low_sleep)

        if DYNAMIC_SLEEP_DEBUG:
            print("")
            print(f"→ Interpolated Base Sleep = {base_sleep:.2f}s "
                  f"(Fraction {frac:.3f} between {anchor_low_sleep}s and {anchor_high_sleep}s)")

        # ------------------------------------------------------------
        # THREAD SCALING
        # ------------------------------------------------------------
        # Gallery threads scale stronger than image threads
        thread_factor = (1 + (gallery_threads - 2) * 0.25) * (1 + (image_threads - 10) * 0.05)

        scaled_sleep = base_sleep * thread_factor * attempt

        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ Thread Factor = {thread_factor:.2f} "
                  f"(Gallery Threads {gallery_threads}, Image Threads {image_threads})")
            print(f"→ Scaled Sleep (with attempt {attempt}) = {scaled_sleep:.2f}s")

        # ------------------------------------------------------------
        # FINAL RANDOMISATION
        # ------------------------------------------------------------
        jitter_min = 0.9
        jitter_max = 1.1
        sleep_time = random.uniform(scaled_sleep * jitter_min,
                                    scaled_sleep * jitter_max)

        if DYNAMIC_SLEEP_DEBUG:
            print("")
            print(f"→ Final Sleep Candidate = {sleep_time:.2f}s "
                  f"(Jitter {jitter_min*100:.0f}% - {jitter_max*100:.0f}%) "
            )

        print(
            f"\n{stage.capitalize()}: Sleep: {sleep_time:.2f}s "
            f"(Load: {total_load} Units)\n------------------------------"
        )
        return sleep_time

# ------------------------------------------------------------
# API STAGE
# ------------------------------------------------------------
    if stage == "api":
        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ API Sleep Min = {api_sleep_min}, API Sleep Max = {api_sleep_max}")    
        
        attempt_scale = attempt ** 2
        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ API Attempt Scale: {attempt_scale}")
        
        base_min, base_max = (api_sleep_min * attempt_scale, api_sleep_max * attempt_scale)
        if DYNAMIC_SLEEP_DEBUG:
            print("")
            print(f"→ API Base Min = {base_min}s, API Base Max = {base_max}s")
        
        sleep_time = random.uniform(base_min, base_max)
        if DYNAMIC_SLEEP_DEBUG:
            print("")
            print(f"→ Sleep Time Candidate = {sleep_time:.2f}s\n"
                  f"  → (Min = {base_min}s)\n"
                  f"  → (Max = {base_max}s)")
        
        print(
            f"\n{stage.capitalize()}: Sleep: {sleep_time:.2f}s\n------------------------------"
        )
        return sleep_time
    
set_num_of_galleries = 200
set_gallery_threads = 2
set_image_threads = 10

for test in range (1, set_num_of_galleries):
    for attempt in range (1, 2):
        #dynamic_sleep("api", attempt=attempt)
        dynamic_sleep("gallery", attempt=attempt)
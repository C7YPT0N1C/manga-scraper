import random

set_num_of_galleries = None
set_gallery_threads = None
set_image_threads = None

def dynamic_sleep(stage, attempt: int = 1):
    """Adaptive sleep timing based on load and stage, 
    including dynamic thread optimisation."""

    DYNAMIC_SLEEP_DEBUG = True  # Enable debug output
    
    # ------------------------------------------------------------
    # Configurable parameters
    # ------------------------------------------------------------
    # Max galleries considered for scaling
    # (values beyond this are clamped for interpolation)
    gallery_cap = 3750
    
    # API sleep ranges (used only in API stage)
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
        
        # If user didn’t manually set threads, optimise them
        gallery_threads = set_gallery_threads
        image_threads = set_image_threads
        if gallery_threads is None or image_threads is None:
            # Gentle capped growth for gallery threads
            #   → increases with gallery count, but maxes at 8
            gallery_threads = min(max(2, int(num_of_galleries / 200) + 1), 8)
            # Image threads scale with gallery threads (1:5 split)
            image_threads = gallery_threads * 5

            if DYNAMIC_SLEEP_DEBUG:
                print(f"→ Optimiser selected threads: {gallery_threads} gallery, {image_threads} image")
        
        # Clamp galleries into interpolation range
        gallery_weight = min(num_of_galleries, gallery_cap)
        
        # Normalised gallery factor (0–1)
        gallery_factor = gallery_weight / gallery_cap
        
        # ------------------------------------------------------------
        # TARGETED SCALING
        # Anchors define the base sleep before thread scaling
        # ------------------------------------------------------------
        anchor_low_galleries = 25
        anchor_low_sleep = 0.5   # base sleep at 25 galleries
        anchor_high_galleries = gallery_cap
        anchor_high_sleep = 2.5  # base sleep at 1250 galleries
        
        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ Number of Galleries = {num_of_galleries} (Capped at {gallery_cap}), Gallery 'Weight' = {gallery_weight}")
            print(f"→ Gallery Threads = {gallery_threads}, Image Threads = {image_threads}")
        
        # Effective concurrency load
        concurrency = gallery_threads * image_threads
        total_load = concurrency * (1 + gallery_factor)
        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ Total Load ({total_load:.2f} Units) = "
                  f"Concurrency ({concurrency}) * (1 + Gallery Factor ({gallery_factor:.3f}))")
        
        # Adjust scaling by attempt number
        load_floor = 10
        load_factor = (total_load / load_floor) * attempt
        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ Load Factor ({load_factor:.2f} Units) = "
                  f"Total Load ({total_load:.2f}) / Load Floor ({load_floor}) * Attempt ({attempt})")

        # Interpolate base sleep between anchor_low and anchor_high
        g = max(anchor_low_galleries,
                min(gallery_weight, anchor_high_galleries))
        frac = ((g - anchor_low_galleries) /
                (anchor_high_galleries - anchor_low_galleries))
        base_sleep = anchor_low_sleep + frac * (anchor_high_sleep - anchor_low_sleep)

        if DYNAMIC_SLEEP_DEBUG:
            print("")
            print(f"→ Interpolated Base Sleep = {base_sleep:.2f}s "
                  f"(Fraction {frac:.3f} between {anchor_low_sleep}s and {anchor_high_sleep}s)")

        # Scale sleep based on threads (more threads = heavier load = longer sleep)
        thread_factor = (1 + (gallery_threads - 2) * 0.25) * (1 + (image_threads - 10) * 0.05)
        scaled_sleep = base_sleep * thread_factor * attempt

        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ Thread Factor = {thread_factor:.2f} "
                  f"(Gallery Threads {gallery_threads}, Image Threads {image_threads})")
            print(f"→ Scaled Sleep (with attempt {attempt}) = {scaled_sleep:.2f}s")

        # Random jitter to avoid predictable sleep times
        jitter_min = 0.9
        jitter_max = 1.1
        sleep_time = random.uniform(scaled_sleep * jitter_min,
                                    scaled_sleep * jitter_max)

        if DYNAMIC_SLEEP_DEBUG:
            print("")
            print(f"→ Final Sleep Candidate = {sleep_time:.2f}s "
                  f"(Jitter {jitter_min*100:.0f}% - {jitter_max*100:.0f}%) ")

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
        
        # Backoff grows quadratically with attempt
        attempt_scale = attempt ** 2
        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ API Attempt Scale: {attempt_scale}")
        
        # Scale base range
        base_min, base_max = (api_sleep_min * attempt_scale, api_sleep_max * attempt_scale)
        if DYNAMIC_SLEEP_DEBUG:
            print("")
            print(f"→ API Base Min = {base_min}s, API Base Max = {base_max}s")
        
        # Pick random sleep in range
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

# ------------------------------
# Test runs
# ------------------------------
set_num_of_galleries = 3000
set_gallery_threads = None   # Let optimiser decide
set_image_threads = None     # Let optimiser decide

for test in range(1, 3):
    for attempt in range(1, 2):
        dynamic_sleep("gallery", attempt=attempt)
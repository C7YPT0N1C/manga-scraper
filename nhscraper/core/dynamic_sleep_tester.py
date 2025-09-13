import random

set_num_of_galleries = None
set_gallery_threads = None
set_image_threads = None

def dynamic_sleep(stage, attempt: int = 1):
    """Adaptive sleep timing based on load and stage, 
    including dynamic thread optimisation with anchor + units scaling."""

    # ------------------------------------------------------------
    # Configurable parameters
    # ------------------------------------------------------------
    # Max galleries considered for scaling
    gallery_cap = 3750 # ~ 150 Pages
    
    api_sleep_min = 0.5
    api_sleep_max = 0.75

    print("------------------------------")
    print(f"{stage.capitalize()} Attempt: {attempt}")

    # ------------------------------------------------------------
    # API STAGE
    # ------------------------------------------------------------
    if stage == "api":
        print(f"→ API Sleep Min = {api_sleep_min}, API Sleep Max = {api_sleep_max}")

        attempt_scale = attempt ** 2
        print(f"→ API Attempt Scale: {attempt_scale}")

        base_min, base_max = api_sleep_min * attempt_scale, api_sleep_max * attempt_scale
        print(f"→ API Base Min = {base_min}s, API Base Max = {base_max}s")

        sleep_time = random.uniform(base_min, base_max)
        print(f"→ Sleep Time Candidate = {sleep_time:.2f}s (Min = {base_min}s, Max = {base_max}s)")

        print(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s")
        print("------------------------------")
        return sleep_time
    
    # ------------------------------------------------------------
    # GALLERY STAGE
    # ------------------------------------------------------------
    if stage == "gallery":
        num_of_galleries = set_num_of_galleries

        # If user didn’t manually set threads, optimise them
        gallery_threads = set_gallery_threads
        image_threads = set_image_threads
        if gallery_threads is None or image_threads is None:
            gallery_threads = min(max(2, int(num_of_galleries / 200) + 1), 8)
            image_threads = gallery_threads * 5
            print(f"→ Optimiser selected threads: {gallery_threads} gallery, {image_threads} image")

        # Clamp galleries into interpolation range
        gallery_weight = min(num_of_galleries, gallery_cap)
        gallery_factor = gallery_weight / gallery_cap

        print(f"→ Number of Galleries = {num_of_galleries} (Capped at {gallery_cap}), Gallery 'Weight' = {gallery_weight}")
        print(f"→ Gallery Threads = {gallery_threads}, Image Threads = {image_threads}")

        # Effective concurrency load
        concurrency = gallery_threads * image_threads
        total_load = concurrency * (1 + gallery_factor)
        print(f"→ Total Load ({total_load:.2f} Units) = Concurrency ({concurrency}) * (1 + Gallery Factor ({gallery_factor:.3f}))")

        # Adjust scaling by attempt number
        load_floor = 10
        load_factor = (total_load / load_floor) * attempt
        print(f"→ Load Factor ({load_factor:.2f} Units) = Total Load ({total_load:.2f}) / Load Floor ({load_floor}) * Attempt ({attempt})")

        # ------------------------------------------------------------
        # Anchor-Based Base Sleep
        # ------------------------------------------------------------
        anchor_low_galleries = 25
        anchor_low_sleep = 0.5 # Seconds
        anchor_high_galleries = gallery_cap
        anchor_high_sleep = 2.5 # Seconds

        g = max(anchor_low_galleries, min(gallery_weight, anchor_high_galleries))
        frac_anchor = (g - anchor_low_galleries) / (anchor_high_galleries - anchor_low_galleries)
        anchor_sleep = anchor_low_sleep + frac_anchor * (anchor_high_sleep - anchor_low_sleep)
        print(f"→ Anchor Base Sleep = {anchor_sleep:.2f}s (Fraction {frac_anchor:.3f})")

        # ------------------------------------------------------------
        # Unit-Based Scaling
        # ------------------------------------------------------------
        min_units, min_mult = 10, 0.5
        max_units, max_mult = 2000, 2.0
        clamped_units = max(min_units, min(total_load, max_units))
        frac_units = (clamped_units - min_units) / (max_units - min_units)
        unit_mult = min_mult + frac_units * (max_mult - min_mult)
        print(f"→ Unit Multiplier = {unit_mult:.2f} (Load {total_load:.2f})")

        # Combine anchor sleep and unit multiplier
        base_sleep = anchor_sleep * unit_mult

        # ------------------------------------------------------------
        # Thread Factor and Attempt Scaling
        # ------------------------------------------------------------
        thread_factor = (1 + (gallery_threads - 2) * 0.25) * (1 + (image_threads - 10) * 0.05)
        scaled_sleep = base_sleep * thread_factor * attempt
        print(f"→ Thread Factor = {thread_factor:.2f} (Gallery Threads {gallery_threads}, Image Threads {image_threads})")
        print(f"→ Scaled Sleep (with attempt {attempt}) = {scaled_sleep:.2f}s")

        # ------------------------------------------------------------
        # Add jitter
        # ------------------------------------------------------------
        jitter_min, jitter_max = 0.9, 1.1
        sleep_time = random.uniform(scaled_sleep * jitter_min, scaled_sleep * jitter_max)
        print(f"→ Final Sleep Candidate = {sleep_time:.2f}s (Jitter {jitter_min*100:.0f}% - {jitter_max*100:.0f}%)")

        print(f"\n{stage.capitalize()}: Sleep: {sleep_time:.2f}s (Load: {total_load:.2f} Units)")
        print("------------------------------\n")
        return sleep_time

# ------------------------------
# Example Test Run
# ------------------------------
set_num_of_galleries = 3000
set_gallery_threads = None
set_image_threads = None

for test in range(1, 5):
    for attempt in range(1, 2):
        dynamic_sleep("gallery", attempt=attempt)
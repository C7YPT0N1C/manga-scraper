import random

set_num_of_galleries = None
set_gallery_threads = None
set_image_threads = None

def dynamic_sleep(stage, attempt: int = 1):
    """Adaptive sleep timing based on load and stage, 
    including dynamic thread optimisation with anchor + units scaling."""

    DYNAMIC_SLEEP_DEBUG = True  # Enable detailed debug logs

    # ------------------------------------------------------------
    # Configurable parameters
    # ------------------------------------------------------------
    gallery_cap = 3750 # Maximum number of galleries considered for scaling (~150 pages)
    gallery_sleep_min = 0.5 # seconds
    api_sleep_min, api_sleep_max = 0.5, 0.75 # API sleep range

    if DYNAMIC_SLEEP_DEBUG:
        print()
        print("------------------------------")
        print(f"{stage.capitalize()} Attempt: {attempt}")
        print()

    # ------------------------------------------------------------
    # API STAGE
    # ------------------------------------------------------------
    if stage == "api":
        attempt_scale = attempt ** 2
        base_min, base_max = api_sleep_min * attempt_scale, api_sleep_max * attempt_scale
        sleep_time = random.uniform(base_min, base_max)
        print()
        print(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s")
        if DYNAMIC_SLEEP_DEBUG:
            print("------------------------------\n")
        return sleep_time

    # ------------------------------------------------------------
    # GALLERY STAGE
    # ------------------------------------------------------------
    if stage == "gallery":
        # --------------------------------------------------------
        # 1. Calculate Galleries / Threads
        # --------------------------------------------------------
        num_of_galleries = set_num_of_galleries
        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ Number of galleries: {num_of_galleries} (Capped at {gallery_cap})")

        gallery_threads = set_gallery_threads
        image_threads = set_image_threads

        if gallery_threads is None or image_threads is None:
            # Base gallery threads = 2, scale with number of galleries
            gallery_threads = max(2, int(num_of_galleries / 500) + 1)  # 500 galleries per thread baseline
            image_threads = gallery_threads * 5  # Keep ratio 1:5
            if DYNAMIC_SLEEP_DEBUG:
                print(f"→ Optimised threads: {gallery_threads} gallery, {image_threads} image")

        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ Gallery weight: {num_of_galleries}")
            print(f"→ Threads: Gallery = {gallery_threads}, Image = {image_threads}")

        # --------------------------------------------------------
        # 2. Calculate total load (Units Of Work)
        # --------------------------------------------------------        
        concurrency = gallery_threads * image_threads
        current_load = (concurrency * attempt) * num_of_galleries
        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ Concurrency = {gallery_threads} Gallery Threads * {image_threads} Image Threads = {concurrency}")
            print(f"→ Current Load = (Concurrency * Attempt) * Gallery Weight = ({concurrency} * {attempt}) * {num_of_galleries} = {current_load:.2f} Units Of Work")

        # --------------------------------------------------------
        # 3. Unit-based scaling
        # --------------------------------------------------------
        unit_factor = (current_load) / gallery_cap
        if DYNAMIC_SLEEP_DEBUG:
            print()
            print(f"→ Unit Factor = {current_load} (Current Load) / {gallery_cap} (Gallery Cap) = {unit_factor:.2f} Units Per Capped Gallery")

        # --------------------------------------------------------
        # 4. Thread factor, attempt scaling, and load factor
        # --------------------------------------------------------
        gallery_thread_multiplier = 0.25
        image_thread_multiplier = 0.05
        
        thread_factor = (1 + (gallery_threads - 2) * gallery_thread_multiplier) * (1 + (image_threads - 10) * image_thread_multiplier)
        scaled_sleep = unit_factor / thread_factor
        
        # Enforce the minimum sleep time
        scaled_sleep = max(scaled_sleep, gallery_sleep_min)
        
        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ Thread factor = (1 + ({gallery_threads}-2)*0.25)*(1 + ({image_threads}-10)*0.05) = {thread_factor:.2f}")
            print(f"→ Scaled sleep = Unit Factor / Thread Factor = {unit_factor:.2f} / {thread_factor:.2f} = {scaled_sleep:.2f}s")

        # --------------------------------------------------------
        # 5. Add jitter to avoid predictable timing
        # --------------------------------------------------------
        jitter_min, jitter_max = 0.9, 1.1
        sleep_time = random.uniform(scaled_sleep * jitter_min, scaled_sleep * jitter_max)
        
        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ Sleep after jitter = Random({scaled_sleep:.2f}*{jitter_min}, {scaled_sleep:.2f}*{jitter_max}) = {sleep_time:.2f}s")

        # --------------------------------------------------------
        # 6. Final result
        # --------------------------------------------------------
        print()
        print(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s (Load: {current_load:.2f} Units)")
        if DYNAMIC_SLEEP_DEBUG:
            print("------------------------------\n")

        return sleep_time

# ------------------------------
# Example Test Run
# ------------------------------
set_num_of_galleries = 100
set_gallery_threads = None
set_image_threads = None

for test in range(1, 5):
    for attempt in range(1, 2):
        dynamic_sleep("gallery", attempt=attempt)
import random

set_gallery_sleep_min = None
set_gallery_sleep_max = None
set_num_of_galleries = None
set_gallery_threads = None
set_image_threads = None

gallery_list = []

def dynamic_sleep(stage, attempt: int = 1):
    """Adaptive sleep timing based on load and stage, 
    including dynamic thread optimisation with anchor + units scaling."""

    DYNAMIC_SLEEP_DEBUG = True  # Enable detailed debug logs

    # ------------------------------------------------------------
    # Configurable parameters
    # ------------------------------------------------------------
    gallery_cap = 3750 # Maximum number of galleries considered for scaling (~150 pages)
    gallery_sleep_min = set_gallery_sleep_min # seconds
    gallery_sleep_max = set_gallery_sleep_max # seconds
    api_sleep_min, api_sleep_max = 0.5, 0.75 # API sleep range

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
        print(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s")
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
        BASE_GALLERY_THREADS = 2
        BASE_IMAGE_THREADS = 10
        
        gallery_thread_damper = 0.9
        image_thread_damper = 0.9

        thread_factor = ((gallery_threads / BASE_GALLERY_THREADS) ** gallery_thread_damper) * ((image_threads / BASE_IMAGE_THREADS) ** image_thread_damper)

        scaled_sleep = unit_factor / thread_factor
        
        # Enforce the minimum sleep time
        scaled_sleep = min(
            max(scaled_sleep, gallery_sleep_min), gallery_sleep_max
            )
        
        if DYNAMIC_SLEEP_DEBUG:
            print(f"→ Thread factor = (({gallery_threads} / {BASE_GALLERY_THREADS}) ** {gallery_thread_damper}) * (({image_threads} / {BASE_IMAGE_THREADS}) ** {image_thread_damper}) = {thread_factor:.2f}")
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

def worst_case_time_estimate(gallery_list):
    current_run_num_of_galleries = len(gallery_list)
    current_run_gallery_threads = set_gallery_threads
    current_run_gallery_sleep_min = set_image_threads
    
    worst_time = (
            (current_run_num_of_galleries / current_run_gallery_threads) *
            current_run_gallery_sleep_min
        )
    
    worst_time_mins = worst_time / 60 # Convert To Minutes
    worst_time_days = worst_time / 60 / 60 # Convert To Hours
    worst_time_hours = worst_time / 60 /60 / 24 # Convert To Days
    
    print (f"Worst Case Time Estimate = {worst_time_mins:.2f} Minutes / {worst_time_days:.2f} Hours / {worst_time_hours:.2f} Days")

# ------------------------------
# Example Test Run
# ------------------------------
set_gallery_sleep_min = 0.5 # Default: 0.5
set_gallery_sleep_max = 100 # Default: 100
set_num_of_galleries = 50
set_gallery_threads = 2 # Default: 2
set_image_threads = 10 # Default: 10
max_attempts = 1

for gallery in range (1, (set_num_of_galleries + 1)):
    gallery_list.append(000)

for test in range(1, set_num_of_galleries):
    for attempt in range(1, (max_attempts + 1)):
        dynamic_sleep("gallery", attempt=attempt)

worst_case_time_estimate(gallery_list)
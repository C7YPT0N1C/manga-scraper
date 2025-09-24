import random

num_of_galleries = None
gallery_list = []

set_gallery_threads = None
threads_galleries = set_gallery_threads
set_image_threads = None
threads_images = set_image_threads

min_sleep = None
max_sleep = None

BATCH_SIZE = 500 # Splits large scrapes into smaller ones
BATCH_SIZE_SLEEP_MULTIPLIER = 0.05 # Seconds to sleep per gallery in batch

def dynamic_sleep(stage, batch_ids = None, attempt: int = 1):
    """
    Adaptive sleep timing based on load and stage, 
    including dynamic thread optimisation with anchor + units scaling.
    """
    
    debug = True  # Forcefully enable detailed debug logs

    # ------------------------------------------------------------
    # Configurable parameters
    # ------------------------------------------------------------
    gallery_cap = 3750 # Maximum number of galleries considered for scaling (~150 pages)
    # min_sleep = Minimum Gallery sleep time
    # max_sleep = Maximum Gallery sleep time
    api_min_sleep, api_max_sleep = 0.5, 0.75 # API sleep range

    print("")
    print("------------------------------", "debug")
    print(f"{stage.capitalize()} Attempt: {attempt}", "debug")
    print("")

    # ------------------------------------------------------------
    # API STAGE
    # ------------------------------------------------------------
    if stage == "api":
        attempt_scale = attempt ** 2
        base_min, base_max = api_min_sleep * attempt_scale, api_max_sleep * attempt_scale
        sleep_time = random.uniform(base_min, base_max)
        print(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s", "debug")
        print("------------------------------", "debug")
        return sleep_time

    # ------------------------------------------------------------
    # GALLERY STAGE
    # ------------------------------------------------------------
    if stage == "gallery":
        # --------------------------------------------------------
        # 1. Calculate Galleries / Threads
        # --------------------------------------------------------
        num_of_galleries = max(1, len(batch_ids))
        
        if debug:
            print(f"→ Number of galleries: {num_of_galleries} (Capped at {gallery_cap})", "debug")

        if threads_galleries is None or threads_images is None:
            # Base gallery threads = 2, scale with number of galleries
            gallery_threads = max(2, int(num_of_galleries / 500) + 1)  # 500 galleries per thread baseline
            image_threads = gallery_threads * 5  # Keep ratio 1:5
            if debug:
                print(f"→ Optimised Threads: {gallery_threads} gallery, {image_threads} image", "debug")
        else:
            gallery_threads = threads_galleries
            image_threads = threads_images
            if debug:
                print(f"→  threads: {gallery_threads} gallery, {image_threads} image", "debug")
                print(f"→ Configured Threads: Gallery = {gallery_threads}, Image = {image_threads}", "debug")

        # --------------------------------------------------------
        # 2. Calculate total load (Units Of Work)
        # --------------------------------------------------------        
        concurrency = gallery_threads * image_threads
        current_load = (concurrency * attempt) * num_of_galleries
        if debug:
            print(f"→ Concurrency = {gallery_threads} Gallery Threads * {image_threads} Image Threads = {concurrency}", "debug")
            print(f"→ Current Load = (Concurrency * Attempt) * Gallery Weight = ({concurrency} * {attempt}) * {num_of_galleries} = {current_load:.2f} Units Of Work", "debug")

        # --------------------------------------------------------
        # 3. Unit-based scaling
        # --------------------------------------------------------
        unit_factor = (current_load) / gallery_cap
        if debug:
            print("")
            print(f"→ Unit Factor = {current_load} (Current Load) / {gallery_cap} (Gallery Cap) = {unit_factor:.2f} Units Per Capped Gallery", "debug")

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
        scaled_sleep = max(scaled_sleep, min_sleep)
        
        if debug:
            print(f"→ Thread factor = (1 + ({gallery_threads}-2)*0.25)*(1 + ({image_threads}-10)*0.05) = {thread_factor:.2f}", "debug")
            print(f"→ Scaled sleep = Unit Factor / Thread Factor = {unit_factor:.2f} / {thread_factor:.2f} = {scaled_sleep:.2f}s", "debug")

        # --------------------------------------------------------
        # 5. Add jitter to avoid predictable timing
        # --------------------------------------------------------
        jitter_min, jitter_max = 0.9, 1.1
        sleep_time = min(random.uniform(scaled_sleep * jitter_min, scaled_sleep * jitter_max), max_sleep)
        
        if debug:
            print(f"→ Sleep after jitter (Capped at {max_sleep}s) = Random({scaled_sleep:.2f}*{jitter_min}, {scaled_sleep:.2f}*{jitter_max}) = {sleep_time:.2f}s", "debug")

        # --------------------------------------------------------
        # 6. Final result
        # --------------------------------------------------------
        print("")
        print(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s (Load: {current_load:.2f} Units)", "debug")
        print("------------------------------", "debug")
        return sleep_time

def worst_case_time_estimate(context: str, id_list: list):
    current_run_num_of_galleries = len(id_list)
    current_batch_sleep_time = BATCH_SIZE * BATCH_SIZE_SLEEP_MULTIPLIER
    
    worst_time_secs = (
        ((current_run_num_of_galleries / threads_galleries) * max_sleep ) +
        ((current_run_num_of_galleries / BATCH_SIZE) * current_batch_sleep_time)
    )
    
    worst_time_mins = worst_time_secs / 60 # Convert To Minutes
    worst_time_days = worst_time_secs / 60 / 60 # Convert To Hours
    worst_time_hours = worst_time_secs / 60 / 60 / 24 # Convert To Days
    
    print("")
    #logger.info(f"Number of Galleries Processed: {len(id_list)}") # DEBUGGING
    #logger.info(f"Number of Threads: Gallery: {threads_galleries}, Image: {threads_images}") # DEBUGGING
    #logger.info(f"Batch Sleep Time: {current_batch_sleep_time:.2f}s per {BATCH_SIZE} galleries") # DEBUGGING
    #logger.info(f"Max Sleep Time: {max_sleep}") # DEBUGGING
    print(f"{context} Worst Case Time Estimate = {worst_time_hours:.2f} Days / {worst_time_days:.2f} Hours / {worst_time_mins:.2f} Minutes")

# ------------------------------
# Example Test Run
# ------------------------------
num_of_galleries = 8349
set_gallery_threads = 2 # Default: 2
set_image_threads = 10 # Default: 10
min_sleep = 0.5 # Default: 0.5
max_sleep = 100 # Default: 100
max_attempts = 1

for gallery in range (1, (num_of_galleries + 1)):
    gallery_list.append(000)

for test in range(1, num_of_galleries):
    for attempt in range(1, (max_attempts + 1)):
        dynamic_sleep("gallery", num_of_galleries, attempt=attempt)

worst_case_time_estimate(gallery_list)
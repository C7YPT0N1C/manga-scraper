import random

def dynamic_sleep(stage, num_galleries: int = 25, num_pages: int = 20, attempt: int = 1): # TEST
    """Adaptive sleep timing based on load and stage"""
    
    # ------------------------------------------------------------
    # Scaling logic
    # ------------------------------------------------------------
    # Scale grows with number of galleries and total concurrency
    #   - More galleries = more cumulative load
    #   - Cap scaling at ×5 to prevent excessive waiting
    #   - Galleries count capped at 1000 to avoid runaway scaling
    
    # ------------------------------------------------------------
    # Define a base sleep range depending on what stage of scraping we're in
    # ------------------------------------------------------------
    
    # Minimum scale value
    scale_min = 0.25
    # Maximum scale value
    scale_max = 60
    
    # Minimum time to sleep
    sleep_min = 0.25
    # Maximum time to sleep
    sleep_max = 0.5 # FROM CONFIG
    
    # How much to multiply sleep time to get gallery sleep time
    gallery_sleep_multiplier = 4
    # Minimum time for a gallery download to sleep
    gallery_sleep_min = (sleep_min * gallery_sleep_multiplier)   
    # Maximum time for a gallery download to sleep
    gallery_sleep_max = (sleep_max * gallery_sleep_multiplier)
    
    if stage == "api":
        # When calling the API, back off more with each retry attempt
        base_min, base_max = (sleep_min * attempt, sleep_max * attempt)

    elif stage == "metadata":
        # Lightweight requests like fetching metadata (fixed short wait)
        base_min, base_max = (sleep_min, sleep_max)

    elif stage == "gallery":
        # Heavier stage (fetching full galleries), so longer base wait
        base_min, base_max = (gallery_sleep_min, gallery_sleep_max)

    # ------------------------------------------------------------
    # GALLERIES
    # ------------------------------------------------------------
    
    print("")
    
    # Max amount of galleries to scale dynamic sleep for before capping out.
    gallery_cap = 3000
    
    # The total number of galleries being processed; at least 1 to avoid division by zero
    num_of_galleries = num_galleries # FROM CONFIG # <-------------- NUMBER OF GALLERIES HERE
    
    # The current number of pages being processed
    num_of_pages = num_pages # <-------------- NUMBER OF PAGES HERE
    
    # The VERY approximate number of total images being processed
    rough_images_weight = min(num_of_galleries, gallery_cap) * num_of_pages
    
    print(f"Rough Image Weight: {rough_images_weight} ( {num_of_galleries} Galleries (Cap of {gallery_cap}) X {num_of_pages} Pages )")
    
    # ------------------------------------------------------------
    # THREADS
    # ------------------------------------------------------------
    
    # The number of threads used to process galleries at once.
    gallery_threads = 2 # FROM CONFIG
    
    # The number of threads used to process images in a gallery at once.
    image_threads = 10 # FROM CONFIG
    
    # Total Threads Used
    #total_threads = (gallery_threads + (gallery_threads * image_threads))
    total_threads = (gallery_threads * image_threads)
    
    print(f"Total Threads: {total_threads} ( {gallery_threads} Gallery Threads X {image_threads} Image Threads )")

    # ------------------------------------------------------------
    # LOAD
    # ------------------------------------------------------------
    
    # Total Load = Rough no of images being downloaded / number of threads downloading them
    total_load = rough_images_weight / total_threads
    print(f"Total Load: {total_load} ( {rough_images_weight} Rough Images Weight / {total_threads} Total Threads )")
    
    # Scale things down
    load_floor = 100
    
    # Total Load / Load Floor
    load_factor = (
        total_load /
        load_floor
    )
    print(f"Load Factor: ~{load_factor} ( {total_load} Total Load / Load Floor of {load_floor} )")

    # ------------------------------------------------------------
    # SCALING AND SLEEP
    # ------------------------------------------------------------
    
    print("")
    
    # Calculate Scale
    scale = min(max(scale_min, load_factor), scale_max)
    print(f"Scale: {scale} ( min( max({scale_min}, {load_factor}) , {scale_max} ) )")
    
    # Choose a random sleep within the scaled range
    sleep_time = random.uniform(base_min * scale, base_max * scale)
    
    print("")
    print(
        f"Sleep Time = {sleep_time}\n"
        f"(Min = base_min * scale = {base_min} * {scale} = {base_min * scale})\n"
        f"(Max = base_max * scale = {base_max} * {scale} = {base_max * scale})"
    )

dynamic_sleep("gallery", 3000, 20) # <-------------- NUMBER OF PAGES HERE
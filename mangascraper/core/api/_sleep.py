# mangascraper/core/api/_sleep.py

import random

from mangascraper.core import orchestrator
from mangascraper.core.orchestrator import log, log_clarification

####################################################################################################################
# SLEEP CLASS
####################################################################################################################

class Sleep:
    """Adaptive retry sleep calculations."""

    @staticmethod
    def calculate_load(stage: str, num_items: int, attempt: int, gallery_cap: int = 3750):
        if orchestrator.threads_galleries is None or orchestrator.threads_images is None:
            gallery_threads = max(2, int(num_items / orchestrator.BATCH_SIZE) + 1) if stage == "gallery" else orchestrator.DEFAULT_THREADS_GALLERIES
            image_threads = gallery_threads * (orchestrator.DEFAULT_THREADS_IMAGES / orchestrator.DEFAULT_THREADS_GALLERIES)
            log(f"→ Optimised Threads: {gallery_threads} Gallery, {image_threads} Image", "debug")
        else:
            gallery_threads = orchestrator.threads_galleries
            image_threads = orchestrator.threads_images
            log(f"→ Threads: {gallery_threads} Gallery, {image_threads} Image", "debug")
            log(f"→ Configured Threads: Gallery = {gallery_threads}, Image = {image_threads}", "debug")

        concurrency = (gallery_threads * image_threads) + gallery_threads
        current_load = (concurrency * attempt) * num_items
        log(f"→ Concurrency = {gallery_threads} Gallery Threads * {image_threads} Image Threads = {concurrency}", "debug")
        log(f"→ Current Load = (Concurrency * Attempt) * Num Of {stage.capitalize()}s = ({concurrency} * {attempt}) * {num_items} = {current_load:.2f} Units Of Work", "debug")

        unit_factor = current_load / gallery_cap
        log_clarification("debug")
        log(f"→ Unit Factor = {current_load} (Current Load) / {gallery_cap} (Gallery Cap) = {unit_factor:.2f} Units Per Capped Gallery", "debug")

        BASE_GALLERY_THREADS = 2
        BASE_IMAGE_THREADS = 10
        gallery_thread_damper = 0.9
        image_thread_damper = 0.9

        thread_factor = (
            (gallery_threads / BASE_GALLERY_THREADS) ** gallery_thread_damper
        ) * (
            (image_threads / BASE_IMAGE_THREADS) ** image_thread_damper
        )
        scaled_sleep = max(unit_factor / thread_factor, orchestrator.min_retry_sleep)

        log(f"→ Thread factor = (({gallery_threads}/{BASE_GALLERY_THREADS})^{gallery_thread_damper}) * (({image_threads}/{BASE_IMAGE_THREADS})^{image_thread_damper}) = {thread_factor:.2f}", "debug")
        log(f"→ Scaled sleep = Unit Factor / Thread Factor = {unit_factor:.2f} / {thread_factor:.2f} = {scaled_sleep:.2f}s", "debug")

        jitter_min, jitter_max = 0.9, 1.1
        sleep_time = min(
            random.uniform(scaled_sleep * jitter_min, scaled_sleep * jitter_max),
            orchestrator.max_retry_sleep,
        )

        log(f"→ Sleep after jitter (Capped at {orchestrator.max_retry_sleep}s) = Random({scaled_sleep:.2f}*{jitter_min}, {scaled_sleep:.2f}*{jitter_max}) = {sleep_time:.2f}s", "debug")

        return sleep_time, current_load, gallery_threads, image_threads, concurrency

    @staticmethod
    def dynamic(stage, attempt: int = 1):
        gallery_cap = 3750

        log_clarification("debug")
        log("------------------------------", "debug")
        log(f"{stage.capitalize()} Attempt: {attempt}", "debug")
        log_clarification("debug")

        if stage == "api":
            attempt_scale = attempt ** 2
            base_min = orchestrator.min_api_sleep * attempt_scale
            base_max = orchestrator.max_api_sleep * attempt_scale
            sleep_time = random.uniform(base_min, base_max)
            log(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s", "debug")
            log("------------------------------", "debug")
            log_clarification()
            return sleep_time

        if stage in ("gallery", "image"):
            num_items = 1 if stage == "gallery" else max(1, orchestrator.total_gallery_images)
            log(f"→ Number of {stage.capitalize()}s: {num_items} (Capped at {gallery_cap})", "debug")
            sleep_time, current_load, gallery_threads, image_threads, concurrency = Sleep.calculate_load(
                stage, num_items, attempt, gallery_cap
            )
            log_clarification("debug")
            log(f"{stage.capitalize()}: Sleep: {sleep_time:.2f}s (Load: {current_load:.2f} Units)", "debug")
            log("------------------------------", "debug")
            log_clarification()
            return sleep_time
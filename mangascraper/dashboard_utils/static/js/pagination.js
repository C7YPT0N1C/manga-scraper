/* Shared pagination helpers for dashboard pages */
(function (global) {
  const Pagination = {
    normalisePageSize(value, options, fallback) {
      const numeric = Number(value);
      if (Number.isFinite(numeric) && Array.isArray(options) && options.includes(numeric)) return numeric;
      return fallback;
    },

    populateItemsPerPageSelect(selectEl, options, currentValue) {
      if (!selectEl) return;
      if (!selectEl.options.length) {
        (options || []).forEach(size => {
          const option = document.createElement('option');
          option.value = String(size);
          option.textContent = String(size);
          selectEl.appendChild(option);
        });
      }
      selectEl.value = String(currentValue);
    },

    getPaginatedSlice(items, page, itemsPerPage) {
      const list = Array.isArray(items) ? items : [];
      const filteredCount = list.length;
      const perPage = Number(itemsPerPage) || 1;
      const totalPages = Math.max(Math.ceil(filteredCount / perPage) || 1, 1);
      const startIdx = (Number(page) - 1) * perPage;
      const endIdx = startIdx + perPage;
      const paginatedItems = list.slice(startIdx, endIdx);
      return { items: paginatedItems, totalPages, filteredCount };
    },

    // Lightweight control updater - keeps existing page state management in templates.
    updateSimpleControls(controlsEl, currentPage, totalPages, prevBtn, nextBtn, pageNumEl, totalPagesEl) {
      if (!controlsEl) return;
      let page = Number(currentPage) || 1;
      if (page > totalPages) page = totalPages;
      if (pageNumEl) pageNumEl.textContent = page;
      if (totalPagesEl) totalPagesEl.textContent = totalPages;
      if (prevBtn) prevBtn.disabled = page === 1;
      if (nextBtn) nextBtn.disabled = page === totalPages;
      controlsEl.style.display = filteredCountIsZero(controlsEl, totalPages) ? 'none' : 'flex';
      function filteredCountIsZero(el, tPages) {
        return tPages <= 0;
      }
    }
  };

  global.Pagination = Pagination;
})(window);

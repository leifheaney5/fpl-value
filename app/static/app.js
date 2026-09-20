document.addEventListener("DOMContentLoaded", () => {
  // Mount help outside scrollable tables so narrow columns cannot clip it.
  let activeHelp = null;
  let closeTimer;
  const closeHelp = () => {
    clearTimeout(closeTimer);
    if (!activeHelp) return;
    activeHelp.tip.classList.remove("is-open");
    activeHelp = null;
  };
  const positionHelp = () => {
    if (!activeHelp) return;
    const { trigger, tip } = activeHelp;
    const anchor = trigger.getBoundingClientRect();
    const box = tip.getBoundingClientRect();
    const padding = 8;
    const left = Math.max(padding, Math.min(anchor.left, window.innerWidth - box.width - padding));
    const below = anchor.bottom + padding;
    const top = below + box.height <= window.innerHeight - padding
      ? below : Math.max(padding, anchor.top - box.height - padding);
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  };
  document.querySelectorAll(".column-heading").forEach((heading, index) => {
    const tip = heading.querySelector(".column-tooltip");
    if (!tip) return;
    const trigger = heading.closest(".sort-button") || heading;
    tip.id = `column-help-${index}`;
    tip.classList.add("column-tooltip--floating");
    document.body.appendChild(tip);
    heading.removeAttribute("title");
    heading.removeAttribute("aria-label");
    trigger.setAttribute("aria-describedby", tip.id);
    const showHelp = () => {
      clearTimeout(closeTimer);
      if (activeHelp?.tip !== tip) closeHelp();
      activeHelp = { trigger, tip };
      tip.classList.add("is-open");
      positionHelp();
    };
    const scheduleClose = () => {
      clearTimeout(closeTimer);
      closeTimer = setTimeout(() => {
        if (activeHelp?.tip === tip && document.activeElement !== trigger) closeHelp();
      }, 150);
    };
    trigger.addEventListener("pointerenter", (event) => {
      if (event.pointerType !== "touch") showHelp();
    });
    trigger.addEventListener("pointerleave", scheduleClose);
    trigger.addEventListener("focus", showHelp);
    trigger.addEventListener("blur", () => {
      if (activeHelp?.tip === tip) closeHelp();
    });
    trigger.addEventListener("click", showHelp);
    trigger.addEventListener("keydown", (event) => {
      if (trigger === heading && ["Enter", " "].includes(event.key)) {
        event.preventDefault();
        showHelp();
      }
    });
    tip.addEventListener("pointerenter", () => clearTimeout(closeTimer));
    tip.addEventListener("pointerleave", scheduleClose);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeHelp();
  });
  document.addEventListener("pointerdown", (event) => {
    if (activeHelp && !activeHelp.trigger.contains(event.target) && !activeHelp.tip.contains(event.target)) closeHelp();
  });
  document.addEventListener("scroll", () => {
    if (!activeHelp) return;
    const anchor = activeHelp.trigger.getBoundingClientRect();
    const container = activeHelp.trigger.closest(".table-wrap")?.getBoundingClientRect();
    if (anchor.bottom <= 0 || anchor.top >= window.innerHeight ||
        anchor.right <= 0 || anchor.left >= window.innerWidth ||
        (container && (anchor.right <= container.left || anchor.left >= container.right ||
          anchor.bottom <= container.top || anchor.top >= container.bottom))) {
      closeHelp();
    } else {
      positionHelp();
    }
  }, true);
  window.addEventListener("resize", closeHelp);

  document.querySelectorAll("table.sortable-table").forEach((table) => {
    const body = table.tBodies[0];
    if (!body) return;
    table.querySelectorAll("button.sort-button").forEach((button) => {
      button.addEventListener("click", () => {
        const index = [...button.closest("tr").children].indexOf(button.closest("th"));
        const ascending = button.dataset.direction !== "asc";
        table.querySelectorAll(".sort-button").forEach((item) => {
          item.dataset.direction = "";
          item.setAttribute("aria-sort", "none");
        });
        button.dataset.direction = ascending ? "asc" : "desc";
        button.setAttribute("aria-sort", ascending ? "ascending" : "descending");
        [...body.rows].sort((a, b) => {
          const left = a.cells[index]?.dataset.sortValue ?? "";
          const right = b.cells[index]?.dataset.sortValue ?? "";
          const leftNumber = left !== "" && !Number.isNaN(Number(left));
          const rightNumber = right !== "" && !Number.isNaN(Number(right));
          let result;
          if (leftNumber && rightNumber) result = Number(left) - Number(right);
          else result = left.localeCompare(right, undefined, {numeric: true, sensitivity: "base"});
          return ascending ? result : -result;
        }).forEach((row) => body.appendChild(row));
      });
    });
  });

  document.querySelectorAll("form.sheet-compare-form").forEach((form) => {
    const boxes = [...form.querySelectorAll('input[type="checkbox"][name="ids"]')];
    const count = form.querySelector("[data-selection-count]");
    const submit = form.querySelector('button[type="submit"]');
    const maximum = Number(form.dataset.maxSelections || 5);
    const update = () => {
      const selected = boxes.filter((box) => box.checked);
      if (selected.length > maximum) {
        selected.at(-1).checked = false;
      }
      const total = boxes.filter((box) => box.checked).length;
      count.textContent = total;
      submit.disabled = total < 2;
    };
    boxes.forEach((box) => box.addEventListener("change", update));
  });
});

document.addEventListener("DOMContentLoaded", () => {
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

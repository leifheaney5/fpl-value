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
});

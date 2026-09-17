function initBoard() {
  document
    .querySelectorAll('.card-list[data-status="a_valider"], .card-list[data-status="a_committer"]')
    .forEach(function (el) {
      new Sortable(el, {
        group: "validation",
        animation: 150,
        onEnd: function (evt) {
          var ticketId = evt.item.dataset.id;
          var newStatus = evt.to.dataset.status;
          fetch("/api/tickets/" + ticketId + "/status", {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: newStatus }),
          }).then(function (r) {
            if (!r.ok) {
              alert("Déplacement refusé");
            }
            htmx.trigger("#board", "refresh");
          });
        },
      });
    });
}

document.body.addEventListener("htmx:afterSwap", function (evt) {
  if (evt.detail.target && evt.detail.target.id === "board") {
    initBoard();
  }
});

if (window.EventSource) {
  var boardEvents = new EventSource("/api/events");
  boardEvents.onmessage = function () {
    htmx.trigger("#board", "refresh");
  };
}

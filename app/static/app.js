// Shared ticket-panel logic: used both by the board's modal and the standalone
// /tickets/{id} page, so the same fragment works in either container.
window.KanbanTicket = (function () {
  var activeStreams = {};

  function toast(message, type) {
    var container = document.getElementById("toast-container");
    if (!container || !message) return;
    var el = document.createElement("div");
    el.className = "toast" + (type ? " " + type : "");
    el.textContent = message;
    container.appendChild(el);
    setTimeout(function () {
      el.remove();
    }, 4500);
  }

  function closeStream(ticketId) {
    if (activeStreams[ticketId]) {
      activeStreams[ticketId].close();
      delete activeStreams[ticketId];
    }
  }

  function bind(container, ticketId) {
    closeStream(ticketId);

    var logsEl = container.querySelector("[data-live-logs]");
    if (logsEl && window.EventSource) {
      var evtSource = new EventSource("/api/tickets/" + ticketId + "/stream");
      evtSource.onmessage = function (e) {
        try {
          var data = JSON.parse(e.data);
          if (data.line) {
            logsEl.textContent += data.line + "\n";
            logsEl.scrollTop = logsEl.scrollHeight;
          }
        } catch (err) {
          /* ignore malformed event */
        }
      };
      activeStreams[ticketId] = evtSource;
    }

    var answerForm = container.querySelector("[data-answer-form]");
    if (answerForm) {
      answerForm.addEventListener("submit", function (e) {
        e.preventDefault();
        var btn = answerForm.querySelector("button[type=submit]");
        var message = new FormData(answerForm).get("message");
        btn.disabled = true;
        btn.textContent = "Envoi…";
        fetch("/api/tickets/" + ticketId + "/answer", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ message: message }),
        })
          .then(function (r) {
            if (!r.ok) throw new Error("request failed");
            toast("Réponse envoyée, l'agent reprend.", "success");
            return load(container, ticketId);
          })
          .catch(function () {
            toast("Échec de l'envoi de la réponse.", "error");
            btn.disabled = false;
            btn.textContent = "Envoyer et reprendre";
          });
      });
    }

    var pushBtn = container.querySelector("[data-push-btn]");
    if (pushBtn) {
      pushBtn.addEventListener("click", function () {
        pushBtn.disabled = true;
        pushBtn.textContent = "Envoi…";
        fetch("/api/tickets/" + ticketId + "/push", { method: "POST" })
          .then(function (r) {
            return r.json();
          })
          .then(function (data) {
            toast(data.message, data.ok ? "success" : "error");
            if (data.ok) {
              return load(container, ticketId);
            }
            pushBtn.disabled = false;
            pushBtn.textContent = "Valider et pousser";
          })
          .catch(function () {
            toast("Échec du push.", "error");
            pushBtn.disabled = false;
            pushBtn.textContent = "Valider et pousser";
          });
      });
    }
  }

  function load(container, ticketId) {
    return fetch("/fragments/tickets/" + ticketId)
      .then(function (r) {
        return r.text();
      })
      .then(function (html) {
        container.innerHTML = html;
        bind(container, ticketId);
        if (window.refreshBoardSoon) window.refreshBoardSoon();
      });
  }

  return { bind: bind, load: load, closeStream: closeStream, toast: toast };
})();

// Board page: card grid, drag & drop, modal orchestration, live refresh.
(function () {
  if (!document.getElementById("board")) return; // not the board page

  var lastFocusedEl = null;
  var currentTicketId = null;

  function initSortable() {
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
                window.KanbanTicket.toast("Déplacement refusé.", "error");
              }
              htmx.trigger("#board", "refresh");
            });
          },
        });
      });
  }

  function bindCardClicks() {
    document.querySelectorAll(".card[data-id]").forEach(function (card) {
      card.addEventListener("click", function () {
        openModal(card.dataset.id);
      });
      card.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openModal(card.dataset.id);
        }
      });
    });
  }

  function openModal(ticketId) {
    var overlay = document.getElementById("modal-overlay");
    var body = document.getElementById("modal-body");
    if (!overlay || !body) return;
    lastFocusedEl = document.activeElement;
    currentTicketId = ticketId;
    body.innerHTML = "Chargement…";
    overlay.classList.add("open");
    history.pushState({ ticket: ticketId }, "", "?ticket=" + ticketId);
    window.KanbanTicket.load(body, ticketId).then(function () {
      var closeBtn = document.getElementById("modal-close-btn");
      if (closeBtn) closeBtn.focus();
    });
  }

  function closeModal(options) {
    var overlay = document.getElementById("modal-overlay");
    var body = document.getElementById("modal-body");
    if (!overlay || !overlay.classList.contains("open")) return;
    overlay.classList.remove("open");
    if (currentTicketId) window.KanbanTicket.closeStream(currentTicketId);
    currentTicketId = null;
    body.innerHTML = "";
    if (!options || !options.skipHistory) {
      history.pushState({}, "", "/");
    }
    if (lastFocusedEl) lastFocusedEl.focus();
  }

  document.getElementById("modal-close-btn").addEventListener("click", function () {
    closeModal();
  });
  document.getElementById("modal-overlay").addEventListener("click", function (e) {
    if (e.target.id === "modal-overlay") closeModal();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeModal();
  });
  window.addEventListener("popstate", function () {
    var params = new URLSearchParams(location.search);
    var ticket = params.get("ticket");
    if (ticket) {
      openModal(ticket);
    } else {
      closeModal({ skipHistory: true });
    }
  });

  document.body.addEventListener("htmx:afterSwap", function (evt) {
    if (evt.detail.target && evt.detail.target.id === "board") {
      initSortable();
      bindCardClicks();
    }
  });

  // Open the ticket named in ?ticket=<id> once the board has rendered at least once.
  var params = new URLSearchParams(location.search);
  var initialTicket = params.get("ticket");
  if (initialTicket) {
    document.body.addEventListener("htmx:afterSwap", function once(evt) {
      if (evt.detail.target && evt.detail.target.id === "board") {
        document.body.removeEventListener("htmx:afterSwap", once);
        openModal(initialTicket);
      }
    });
  }

  window.refreshBoardSoon = function () {
    htmx.trigger("#board", "refresh");
  };

  if (window.EventSource) {
    var boardEvents = new EventSource("/api/events");
    boardEvents.onmessage = function (e) {
      htmx.trigger("#board", "refresh");
      try {
        var data = JSON.parse(e.data);
        if (currentTicketId && String(data.ticket_id) === String(currentTicketId)) {
          window.KanbanTicket.load(document.getElementById("modal-body"), currentTicketId);
        }
      } catch (err) {
        /* ignore malformed event */
      }
    };
  }
})();

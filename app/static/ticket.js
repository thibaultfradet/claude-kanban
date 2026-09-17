(function () {
  var logsEl = document.getElementById("live-logs");
  if (window.EventSource && logsEl) {
    var evtSource = new EventSource("/api/tickets/" + window.TICKET_ID + "/stream");
    evtSource.onmessage = function (e) {
      var data = JSON.parse(e.data);
      if (data.line) {
        logsEl.textContent += data.line + "\n";
        logsEl.scrollTop = logsEl.scrollHeight;
      }
    };
  }

  var answerForm = document.getElementById("answer-form");
  if (answerForm) {
    answerForm.addEventListener("submit", function (e) {
      e.preventDefault();
      var message = new FormData(answerForm).get("message");
      fetch("/api/tickets/" + window.TICKET_ID + "/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: message }),
      }).then(function () {
        location.reload();
      });
    });
  }

  var pushBtn = document.getElementById("push-btn");
  if (pushBtn) {
    pushBtn.addEventListener("click", function () {
      pushBtn.disabled = true;
      fetch("/api/tickets/" + window.TICKET_ID + "/push", { method: "POST" })
        .then(function (r) {
          return r.json();
        })
        .then(function (data) {
          alert(data.message);
          location.reload();
        });
    });
  }
})();

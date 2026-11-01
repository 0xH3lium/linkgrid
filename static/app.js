(() => {
  const copyAnnouncement = document.querySelector(".copy-announcement");

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }

    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.className = "sr-only";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    if (!copied) {
      throw new Error("Copy command was rejected");
    }
  }

  document.querySelectorAll("[data-copy], [data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const copyLabel = button.querySelector(".copy-label");
      const originalText = copyLabel ? copyLabel.textContent : "";
      const originalLabel = button.getAttribute("aria-label") || "Copy";
      const originalTitle = button.getAttribute("title") || originalLabel;
      let value = button.dataset.copy;

      if (!value && button.dataset.copyTarget) {
        const target = document.querySelector(button.dataset.copyTarget);
        value = target ? target.textContent : "";
      }

      try {
        if (!value) {
          throw new Error("Nothing to copy");
        }
        await copyText(value);
        button.classList.add("is-copied");
        button.setAttribute("aria-label", "Copied");
        button.setAttribute("title", "Copied");
        if (copyLabel) {
          copyLabel.textContent = "Copied";
        }
        if (copyAnnouncement) {
          copyAnnouncement.textContent = "Copied to clipboard.";
        }
        window.setTimeout(() => {
          button.classList.remove("is-copied");
          button.setAttribute("aria-label", originalLabel);
          button.setAttribute("title", originalTitle);
          if (copyLabel) {
            copyLabel.textContent = originalText;
          }
          if (copyAnnouncement) {
            copyAnnouncement.textContent = "";
          }
        }, 1800);
      } catch (_error) {
        if (copyAnnouncement) {
          copyAnnouncement.textContent = "Could not copy. Select the content and copy it manually.";
        }
      }
    });
  });

  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) {
        event.preventDefault();
      }
    });
  });
})();

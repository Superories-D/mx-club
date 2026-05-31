document.addEventListener("submit", function (event) {
  const form = event.target;
  const message = form.getAttribute("data-confirm");
  if (message && !window.confirm(message)) {
    event.preventDefault();
  }
});

document.addEventListener("click", function (event) {
  const trigger = event.target.closest("[data-confirm-click]");
  if (trigger && !window.confirm(trigger.getAttribute("data-confirm-click"))) {
    event.preventDefault();
  }
});

document.addEventListener("DOMContentLoaded", function () {
  const fileInput = document.querySelector("[data-avatar-input]");
  const cropEditor = document.querySelector("[data-avatar-crop]");
  const canvas = document.querySelector("[data-avatar-canvas]");
  const preview = document.querySelector("[data-avatar-current]");
  const xInput = document.querySelector("[data-avatar-x]");
  const yInput = document.querySelector("[data-avatar-y]");
  const zoomInput = document.querySelector("[data-avatar-zoom]");
  const presetInputs = document.querySelectorAll("input[name='avatar_preset']");
  if (!fileInput || !cropEditor || !canvas || !preview || !xInput || !yInput || !zoomInput) {
    return;
  }

  const context = canvas.getContext("2d");
  const image = new Image();
  let objectUrl = "";

  function drawCrop() {
    if (!image.naturalWidth || !image.naturalHeight) {
      return;
    }
    const zoom = Number(zoomInput.value) / 100;
    const side = Math.min(image.naturalWidth, image.naturalHeight) / zoom;
    const sourceX = (image.naturalWidth - side) * Number(xInput.value) / 100;
    const sourceY = (image.naturalHeight - side) * Number(yInput.value) / 100;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, sourceX, sourceY, side, side, 0, 0, canvas.width, canvas.height);
  }

  fileInput.addEventListener("change", function () {
    const file = fileInput.files[0];
    if (!file) {
      cropEditor.hidden = true;
      return;
    }
    presetInputs.forEach(function (input) { input.checked = false; });
    if (objectUrl) {
      URL.revokeObjectURL(objectUrl);
    }
    objectUrl = URL.createObjectURL(file);
    image.onload = function () {
      cropEditor.hidden = false;
      preview.src = objectUrl;
      drawCrop();
    };
    image.src = objectUrl;
  });

  [xInput, yInput, zoomInput].forEach(function (input) {
    input.addEventListener("input", drawCrop);
  });

  presetInputs.forEach(function (input) {
    input.addEventListener("change", function () {
      if (input.checked) {
        fileInput.value = "";
        cropEditor.hidden = true;
        preview.src = input.value;
      }
    });
  });
});

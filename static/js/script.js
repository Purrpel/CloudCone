document.addEventListener('DOMContentLoaded', function () {
  console.log("Script loaded.");

  const copyButton = document.querySelector('#copy-button');
  const userKeyElement = document.querySelector('#user-key');

  if (!copyButton || !userKeyElement) {
    console.error("Copy button or user key element not found.");
    return;
  }

  copyButton.addEventListener('click', function () {
    const userKey = userKeyElement.textContent.trim(); // Only the key text will be copied

    if (!userKey) {
      alert("No widget key found!");
      return;
    }

    navigator.clipboard.writeText(userKey)
      .then(() => {
        alert('✅ Widget key copied successfully!');
      })
      .catch((err) => {
        console.error("Copy failed:", err);
        alert('❌ Failed to copy: ' + err);
      });
  });
});

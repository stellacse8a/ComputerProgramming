const dialog = document.getElementById('confirm-dialog');
let pendingForm = null;
document.querySelectorAll('form[data-confirm]').forEach(form => {
  form.addEventListener('submit', event => {
    event.preventDefault();
    pendingForm = form;
    const quantity = form.querySelector('[name="quantity"]').value;
    document.getElementById('confirm-text').textContent = `${form.dataset.confirm} (${quantity}개)`;
    dialog.showModal();
  });
});
document.getElementById('cancel-confirm').onclick = () => { pendingForm = null; dialog.close(); };
document.getElementById('accept-confirm').onclick = () => {
  if (pendingForm) { const form = pendingForm; pendingForm = null; form.submit(); }
  dialog.close();
};
dialog.addEventListener('cancel', () => { pendingForm = null; });

/* A menu button's popup, shared by Stella's location path and the companion switcher on both pages.
   One popup element serves every button attached to it; the caller builds the items each time it opens. */
const annaMenu = (() => {
  function create(menu, {items, place = null, adjacent = null}) {
    let owner = null;
    const choices = () => [...menu.querySelectorAll('[role=menuitem]:not([aria-disabled=true])')];
    function open(button, focus = 'current') {
      close(false);
      // Plain elements become items; a group keeps its role and labels the items it holds.
      menu.replaceChildren(...items(button).map(item => {
        if (!item.hasAttribute('role')) { item.setAttribute('role','menuitem'); item.tabIndex = -1; }
        return item;
      }));
      menu.setAttribute('aria-labelledby', button.id);
      owner = button; button.setAttribute('aria-expanded','true'); menu.hidden = false;
      place?.(menu, button);
      const list = choices();
      const target = focus === 'last' ? list.at(-1) : list.find(item => item.hasAttribute('aria-current')) || list[0];
      if (target) { target.focus({preventScroll:true}); target.scrollIntoView({block:'nearest'}); } else menu.focus();
    }
    function close(focusOwner) {
      const closing = owner;
      if (!closing) return;
      // Items stay until the next opening: a chosen link must still be in the document to navigate.
      owner = null; closing.setAttribute('aria-expanded','false');
      menu.hidden = true;
      if (focusOwner) closing.focus();
    }
    // Buttons need an id: the open popup is labelled by the button that opened it.
    function bind(button) {
      button.setAttribute('aria-haspopup','menu'); button.setAttribute('aria-expanded','false');
      button.setAttribute('aria-controls',menu.id);
    }
    const ownedBy = button => button?.matches(`[aria-haspopup=menu][aria-controls="${menu.id}"]`);
    function leave(event) {
      if (owner && !menu.contains(event.relatedTarget) && event.relatedTarget !== owner) close(false);
    }
    // A container holds the buttons; clicks toggle and the vertical arrows open from the first or last choice.
    function attach(container) {
      container.addEventListener('click', event => {
        const button = event.target.closest('button');
        if (ownedBy(button)) owner === button ? close(true) : open(button);
      });
      container.addEventListener('keydown', event => {
        const button = event.target.closest('button');
        if (ownedBy(button) && ['ArrowDown','ArrowUp'].includes(event.key)) {
          event.preventDefault(); open(button, event.key === 'ArrowUp' ? 'last' : 'current');
        }
      });
      container.addEventListener('focusout', leave);
    }
    menu.addEventListener('focusout', leave);
    // Choosing an item closes the menu first; links then navigate as links do.
    menu.addEventListener('click', event => {
      if (event.target.closest('[role=menuitem]:not([aria-disabled=true])')) close(true);
    });
    menu.addEventListener('keydown', event => {
      const list = choices(), index = list.indexOf(document.activeElement);
      let next;
      if (event.key === 'ArrowDown') next = list[(index+1) % list.length];
      else if (event.key === 'ArrowUp') next = list[(index-1+list.length) % list.length];
      else if (event.key === 'Home') next = list[0];
      else if (event.key === 'End') next = list.at(-1);
      else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true); return; }
      else if (event.key === 'Tab') { close(false); return; }
      else if (event.key === ' ' && index >= 0) { event.preventDefault(); list[index].click(); return; }
      else if (['ArrowLeft','ArrowRight'].includes(event.key)) {
        const beside = adjacent?.(owner, event.key === 'ArrowLeft' ? -1 : 1);
        if (beside) { event.preventDefault(); open(beside); }
        return;
      } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && event.key.trim()) {
        // Type-ahead: the next item whose name starts with the typed character.
        const key = event.key.toLocaleLowerCase(), ordered = [...list.slice(index+1), ...list.slice(0,index+1)];
        next = ordered.find(item => item.textContent.trim().toLocaleLowerCase().startsWith(key));
        if (!next) return;
      } else return;
      event.preventDefault(); next?.focus(); next?.scrollIntoView({block:'nearest'});
    });
    return {open, close, bind, attach, get owner() { return owner; }};
  }
  return {create};
})();

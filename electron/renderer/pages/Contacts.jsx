import { useEffect, useMemo, useState } from 'react';

const STORAGE_KEY = 'paris_sender_contacts';

function readContacts() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'); } catch { return []; }
}

function normalizeEmail(email) {
  return email.trim().toLowerCase();
}

export default function Contacts() {
  const [contacts, setContacts] = useState(readContacts);
  const [email, setEmail] = useState('');
  const [paste, setPaste] = useState('');

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(contacts));
  }, [contacts]);

  const sortedContacts = useMemo(() => [...contacts].sort(), [contacts]);

  function addMany(values) {
    const next = new Set(contacts);
    values.map(normalizeEmail).filter((value) => /.+@.+\..+/.test(value)).forEach((value) => next.add(value));
    setContacts([...next]);
  }

  function addSingle(event) {
    event.preventDefault();
    addMany([email]);
    setEmail('');
  }

  function importPaste() {
    addMany(paste.split(/\s+/));
    setPaste('');
  }

  return (
    <div className="grid two">
      <section className="card">
        <h2>Add recipients</h2>
        <form onSubmit={addSingle}>
          <div className="form-row">
            <label>Email address</label>
            <input value={email} onChange={(event) => setEmail(event.target.value)} placeholder="person@example.com" />
          </div>
          <button className="primary" type="submit">Add contact</button>
        </form>
        <div className="form-row" style={{ marginTop: 18 }}>
          <label>Import pasted emails, one per line</label>
          <textarea value={paste} onChange={(event) => setPaste(event.target.value)} placeholder="alice@example.com&#10;bob@example.com" />
        </div>
        <button className="secondary" onClick={importPaste} type="button">Import list</button>
      </section>
      <section className="card">
        <div className="card-header">
          <h2>Recipients ({contacts.length})</h2>
          <button className="ghost small" onClick={() => setContacts([])} type="button">Clear</button>
        </div>
        <div className="list">
          {sortedContacts.map((contact) => (
            <div className="list-item card-header" key={contact}>
              <span>{contact}</span>
              <button className="ghost small" onClick={() => setContacts(contacts.filter((item) => item !== contact))} type="button">Remove</button>
            </div>
          ))}
          {contacts.length === 0 && <p className="muted">No recipients saved yet. Campaign Manager reads this local list when sending.</p>}
        </div>
      </section>
    </div>
  );
}

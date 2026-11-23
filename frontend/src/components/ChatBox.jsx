// ChatBox.jsx
// Chat interface: message list, streaming answers over SSE, typing state and input.
// A new chat is started by remounting this component (App changes its key),
// which also gives it a fresh session id for the backend's conversation memory.

import { useEffect, useRef, useState } from 'react';
import Message from './Message';
import Welcome from './Welcome';
import { ApiError, streamChat } from '../api';
import { newSessionId } from '../format';
import { useI18n } from '../i18n/context';

const getTimestamp = () => new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });

const ChatBox = ({ fiscalYear, currentFy }) => {
  const { t } = useI18n();
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [sessionId] = useState(newSessionId);

  const bottomRef = useRef(null);
  const textareaRef = useRef(null);
  const abortRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const updateBot = (id, patch) =>
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...(typeof patch === 'function' ? patch(m) : patch) } : m)));

  const send = async (raw) => {
    const question = raw.trim();
    if (!question || busy) return;

    const now = Date.now();
    const botId = now + 1;
    setMessages((prev) => [
      ...prev,
      { id: now, sender: 'user', text: question, timestamp: getTimestamp() },
      { id: botId, sender: 'bot', text: '', streaming: true, pending: true, timestamp: getTimestamp() },
    ]);
    setInput('');
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    setBusy(true);

    const controller = new AbortController();
    abortRef.current = controller;
    let failed = null;

    try {
      await streamChat(
        { message: question, session_id: sessionId, fiscal_year: fiscalYear || null },
        {
          onMeta: (meta) =>
            updateBot(botId, {
              fyUsed: meta.fiscal_year_used,
              fyWarning: meta.fy_warning,
              sources: meta.sources,
              calculation: meta.calculation || null,
            }),
          onToken: (text) => updateBot(botId, (m) => ({ text: m.text + text, pending: false })),
          onDone: (result) =>
            updateBot(botId, {
              text: result.answer,
              fyUsed: result.fiscal_year_used,
              fyWarning: result.fy_warning,
              sources: result.sources,
              calculation: result.calculation,
              streaming: false,
              pending: false,
            }),
          onError: (detail) => {
            failed = detail || t.errorGeneric;
          },
        },
        controller.signal,
      );
    } catch (err) {
      failed = err instanceof ApiError && err.status === 0 ? t.errorConnect : err.message || t.errorGeneric;
    } finally {
      if (failed) updateBot(botId, { text: failed, isError: true, streaming: false, pending: false });
      else updateBot(botId, { streaming: false, pending: false });
      setBusy(false);
      textareaRef.current?.focus();
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send(input);
    }
  };

  const handleInput = (e) => {
    setInput(e.target.value);
    e.target.style.height = 'auto';
    e.target.style.height = `${Math.min(e.target.scrollHeight, 140)}px`;
  };

  const pending = messages.some((m) => m.pending);

  return (
    <div className="flex flex-col flex-1 max-w-3xl w-full mx-auto px-3 sm:px-5 py-3 gap-3 overflow-hidden min-h-0">
      <div className="flex-1 overflow-y-auto chat-scroll min-h-0 px-1" role="log" aria-live="polite" aria-busy={busy}>
        {messages.length === 0 && (
          <Welcome
            currentFy={currentFy}
            onPick={(s) => {
              setInput(s);
              textareaRef.current?.focus();
            }}
          />
        )}

        {messages
          .filter((m) => !m.pending)
          .map((m) => (
            <Message key={m.id} message={m} />
          ))}

        {pending && (
          <div className="flex justify-start mb-4 items-end gap-2" aria-label={t.typing}>
            <div className="bg-white border border-gray-100 rounded-2xl rounded-bl-sm px-4 py-3 shadow-sm flex items-center gap-2">
              <span className="flex gap-1" aria-hidden="true">
                <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <span className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </span>
              <span className="text-xs text-gray-500">{t.typing}</span>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <form
        className="bg-white rounded-2xl shadow-md border border-gray-200 overflow-hidden focus-within:ring-2 focus-within:ring-nepal-blue/30"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <div className="flex items-end gap-2 px-4 py-3">
          <textarea
            ref={textareaRef}
            className="flex-1 resize-none outline-none text-sm text-gray-800 placeholder-gray-400 leading-relaxed bg-transparent"
            style={{ maxHeight: '140px', minHeight: '24px' }}
            rows={1}
            maxLength={2000}
            placeholder={t.placeholder}
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            aria-label={t.inputLabel}
          />
          <button
            type="submit"
            disabled={!input.trim() || busy}
            className="flex-shrink-0 bg-gradient-to-br from-nepal-red to-nepal-red-dark hover:brightness-110 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold text-sm px-5 py-2.5 rounded-xl transition-all duration-200 shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-nepal-red/50"
            aria-label={t.sendLabel}
          >
            {t.send} ↑
          </button>
        </div>
        <div className="flex items-center justify-between px-4 py-2 bg-gray-50 border-t border-gray-100 gap-2">
          <span className="text-[11px] text-gray-400 hidden sm:inline">{t.enterHint}</span>
          <span className="text-[11px] text-gray-400">{t.disclaimerFooter}</span>
        </div>
      </form>
    </div>
  );
};

export default ChatBox;

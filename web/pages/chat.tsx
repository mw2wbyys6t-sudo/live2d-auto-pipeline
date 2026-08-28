import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Send,
  Mic,
  MicOff,
  Trash2,
  Volume2,
  VolumeX,
  Bot,
  Sparkles,
  Settings2,
  HelpCircle,
} from 'lucide-react';
import type { NextPage } from 'next';
import type { Character, ChatMessage, Emotion } from '../types';
import { apiClient } from '../lib/api-client';
import ChatBubble from '../components/ChatBubble';
import Modal from '../components/Modal';

const SUGGESTED_EMOTIONS: Emotion[] = [
  'neutral',
  'happy',
  'sad',
  'angry',
  'surprised',
  'shy',
  'thinking',
  'excited',
];

function uid(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

const ChatPage: NextPage = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'welcome',
      role: 'assistant',
      content:
        "Hi there! I'm your Live2D companion. Ask me anything — I can help with character design, pipeline tips, or just chat. ✨",
      emotion: 'happy',
      timestamp: new Date().toISOString(),
    },
  ]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [listening, setListening] = useState(false);
  const [tts, setTts] = useState(false);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [characterId, setCharacterId] = useState<string | undefined>();
  const [personality, setPersonality] = useState('Cheerful, curious, and helpful. Uses casual anime expressions.');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [currentEmotion, setCurrentEmotion] = useState<Emotion>('happy');
  const recognitionRef = useRef<any>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    apiClient
      .getCharacters()
      .then((c) => {
        setCharacters(c);
        if (c.length > 0) setCharacterId(c[0].id);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const el = scrollRef.current as (HTMLDivElement & { scrollTo?: (opts: ScrollToOptions) => void }) | null;
    if (el && typeof el.scrollTo === 'function') {
      el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
    } else if (el) {
      // Fallback for environments without Element.scrollTo
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, sending]);

  const speak = useCallback(
    (text: string) => {
      if (!tts || typeof window === 'undefined' || !('speechSynthesis' in window)) return;
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.rate = 1.05;
      u.pitch = 1.2;
      window.speechSynthesis.speak(u);
    },
    [tts],
  );

  const send = useCallback(async () => {
    const content = input.trim();
    if (!content || sending) return;
    const userMsg: ChatMessage = {
      id: uid(),
      role: 'user',
      content,
      timestamp: new Date().toISOString(),
    };
    const assistantId = uid();
    const assistantMsg: ChatMessage = {
      id: assistantId,
      role: 'assistant',
      content: '',
      emotion: 'thinking',
      timestamp: new Date().toISOString(),
    };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);
    setInput('');
    setSending(true);

    let full = '';
    const onChunk = (chunk: string) => {
      full += chunk;
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId ? { ...m, content: full, emotion: 'thinking' } : m,
        ),
      );
    };

    try {
      const history: ChatMessage[] = [...messages, userMsg];
      await apiClient.chat(history, onChunk, characterId).catch(async () => {
        // fallback local response
        const fallback = generateFallback(content, personality);
        for (const word of fallback.text.split(' ')) {
          await new Promise((r) => setTimeout(r, 40));
          onChunk(word + ' ');
        }
        setCurrentEmotion(fallback.emotion);
      });
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, emotion: detectEmotion(full) || 'happy' }
            : m,
        ),
      );
      setCurrentEmotion(detectEmotion(full) || 'happy');
      speak(full);
    } catch (err) {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? {
                ...m,
                content: err instanceof Error ? err.message : 'Something went wrong',
                emotion: 'sad',
              }
            : m,
        ),
      );
    } finally {
      setSending(false);
    }
  }, [input, sending, messages, characterId, personality, speak]);

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  const toggleVoice = () => {
    const SR =
      (typeof window !== 'undefined' &&
        ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition)) ||
      null;
    if (!SR) {
      alert('Speech recognition not supported in this browser');
      return;
    }
    if (listening) {
      recognitionRef.current?.stop();
      setListening(false);
      return;
    }
    const rec = new SR();
    rec.continuous = false;
    rec.interimResults = false;
    rec.lang = 'en-US';
    rec.onresult = (event: any) => {
      const text = event.results[0][0].transcript;
      setInput((prev) => (prev ? prev + ' ' : '') + text);
    };
    rec.onend = () => setListening(false);
    rec.onerror = () => setListening(false);
    rec.start();
    recognitionRef.current = rec;
    setListening(true);
  };

  const clearChat = () => {
    setMessages([
      {
        id: uid(),
        role: 'assistant',
        content: 'Conversation cleared. What would you like to talk about?',
        emotion: 'happy',
        timestamp: new Date().toISOString(),
      },
    ]);
  };

  const currentCharacter = Array.isArray(characters) ? characters.find((c) => c.id === characterId) : undefined;

  return (
    <div className="animate-fade-in">
      <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <Sparkles className="w-6 h-6 text-pink-400" /> AI Chat
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Conversation with emotion-aware responses and TTS
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setTts((t) => !t)}
            className={`inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs border transition-colors ${
              tts
                ? 'bg-emerald-500/10 border-emerald-500/40 text-emerald-300'
                : 'bg-gray-800 border-gray-700 text-gray-300'
            }`}
          >
            {tts ? <Volume2 className="w-3.5 h-3.5" /> : <VolumeX className="w-3.5 h-3.5" />}
            TTS
          </button>
          <button
            onClick={() => setSettingsOpen(true)}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs bg-gray-800 border border-gray-700 text-gray-300 hover:bg-gray-700"
          >
            <Settings2 className="w-3.5 h-3.5" /> Persona
          </button>
          <button
            onClick={clearChat}
            className="inline-flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs bg-red-500/10 border border-red-500/30 text-red-300 hover:bg-red-500/20"
          >
            <Trash2 className="w-3.5 h-3.5" /> Clear
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_280px] gap-4">
        {/* Chat */}
        <div className="bg-[#1a1a23] border border-gray-800 rounded-xl flex flex-col h-[calc(100vh-220px)] min-h-[500px] overflow-hidden">
          {/* Character header */}
          <div className="px-4 py-3 border-b border-gray-800 flex items-center gap-3">
            <div className="relative">
              {currentCharacter?.thumbnailUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={currentCharacter.thumbnailUrl}
                  alt=""
                  className="w-10 h-10 rounded-full object-cover border-2 border-pink-500/40"
                />
              ) : (
                <div className="w-10 h-10 rounded-full bg-gradient-to-br from-pink-500 to-purple-600 flex items-center justify-center">
                  <Bot className="w-5 h-5 text-white" />
                </div>
              )}
              <span
                className={`absolute -bottom-0.5 -right-0.5 w-3 h-3 rounded-full border-2 border-[#1a1a23] ${emotionDotColor(
                  currentEmotion,
                )}`}
              />
            </div>
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-white truncate">
                {currentCharacter?.name || 'Assistant'}
              </p>
              <p className="text-[11px] text-gray-500 capitalize flex items-center gap-1">
                <span className={`w-1.5 h-1.5 rounded-full ${emotionDotColor(currentEmotion)}`} />
                Feeling {currentEmotion}
              </p>
            </div>
            <select
              value={characterId || ''}
              onChange={(e) => setCharacterId(e.target.value || undefined)}
              className="px-2.5 py-1.5 bg-gray-900 border border-gray-700 rounded-md text-xs text-white focus:outline-none focus:border-pink-500"
            >
              <option value="">Default assistant</option>
              {characters.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>

          {/* Messages */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-4">
            {messages.map((m) => (
              <ChatBubble
                key={m.id}
                message={m}
                characterName={currentCharacter?.name || 'Assistant'}
                avatarUrl={currentCharacter?.thumbnailUrl}
              />
            ))}
          </div>

          {/* Emotion quick-select */}
          <div className="px-4 py-2 border-t border-gray-800 flex gap-1 overflow-x-auto">
            {SUGGESTED_EMOTIONS.map((em) => (
              <button
                key={em}
                onClick={() => setCurrentEmotion(em)}
                className={`shrink-0 px-2.5 py-1 rounded-full text-[10px] border capitalize transition-colors ${
                  currentEmotion === em
                    ? 'bg-pink-500/20 border-pink-500/50 text-pink-300'
                    : 'bg-gray-900 border-gray-800 text-gray-500 hover:text-gray-300'
                }`}
              >
                {em}
              </button>
            ))}
          </div>

          {/* Input */}
          <div className="p-3 border-t border-gray-800 flex items-end gap-2">
            <button
              onClick={toggleVoice}
              className={`p-2.5 rounded-lg transition-colors ${
                listening
                  ? 'bg-red-500/20 text-red-300 animate-pulse'
                  : 'bg-gray-800 text-gray-400 hover:text-white hover:bg-gray-700'
              }`}
              aria-label="Voice input"
            >
              {listening ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
            </button>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              rows={1}
              placeholder="Type a message…  (Enter to send, Shift+Enter for newline)"
              className="flex-1 resize-none px-3 py-2.5 bg-gray-900 border border-gray-700 rounded-lg text-sm text-white placeholder:text-gray-600 focus:outline-none focus:border-pink-500 max-h-32"
            />
            <button
              onClick={send}
              disabled={!input.trim() || sending}
              className="p-2.5 rounded-lg bg-gradient-to-r from-pink-500 to-purple-600 text-white disabled:opacity-50 disabled:cursor-not-allowed hover:shadow-lg hover:shadow-pink-500/30 transition-all"
              aria-label="Send"
            >
              <Send className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Side panel */}
        <div className="space-y-3">
          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-4">
            <p className="text-xs font-medium text-gray-400 mb-3 flex items-center gap-1.5">
              <HelpCircle className="w-3.5 h-3.5" /> Voice commands
            </p>
            <ul className="space-y-1.5 text-[11px] text-gray-500">
              <li>• Tap mic and speak — we transcribe for you</li>
              <li>• TTS reads responses aloud</li>
              <li>• Emotions are detected & shown on avatar</li>
              <li>• Switch characters via the dropdown</li>
            </ul>
          </div>

          <div className="bg-[#1a1a23] border border-gray-800 rounded-xl p-4">
            <p className="text-xs font-medium text-gray-400 mb-3">Mood log</p>
            <div className="space-y-1.5">
              {SUGGESTED_EMOTIONS.slice(0, 5).map((em) => (
                <div key={em} className="flex items-center justify-between text-xs">
                  <span className="text-gray-400 capitalize flex items-center gap-2">
                    <span className={`w-2 h-2 rounded-full ${emotionDotColor(em)}`} />
                    {em}
                  </span>
                  <span className="text-gray-600 font-mono">
                    {em === currentEmotion ? 'now' : '—'}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <Modal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        title="Persona settings"
        size="md"
        footer={
          <button
            onClick={() => setSettingsOpen(false)}
            className="px-4 py-2 rounded-lg bg-gradient-to-r from-pink-500 to-purple-600 text-white text-sm font-medium"
          >
            Done
          </button>
        }
      >
        <label className="block">
          <span className="block text-xs font-medium text-gray-400 mb-2">Personality prompt</span>
          <textarea
            value={personality}
            onChange={(e) => setPersonality(e.target.value)}
            rows={5}
            className="w-full px-3 py-2 bg-gray-900 border border-gray-700 rounded-lg text-sm text-white focus:outline-none focus:border-pink-500 resize-none"
          />
          <p className="mt-2 text-[11px] text-gray-500">
            This system prompt shapes how the assistant responds.
          </p>
        </label>
      </Modal>
    </div>
  );
};

function emotionDotColor(emotion: Emotion): string {
  switch (emotion) {
    case 'happy':
    case 'excited':
      return 'bg-emerald-400';
    case 'sad':
      return 'bg-blue-400';
    case 'angry':
      return 'bg-red-400';
    case 'surprised':
      return 'bg-amber-400';
    case 'shy':
      return 'bg-pink-400';
    case 'thinking':
      return 'bg-purple-400';
    default:
      return 'bg-gray-400';
  }
}

function detectEmotion(text: string): Emotion | null {
  const lower = text.toLowerCase();
  if (/haha|lol|yay|!{2,}|\bhap|great|awesome|love/.test(lower)) return 'happy';
  if (/\?{2,}|whoa|wow|really\?/.test(lower)) return 'surprised';
  if (/sad|sorry|unfortunately|can't/.test(lower)) return 'sad';
  if (/angry|mad|hate|frustrat/.test(lower)) return 'angry';
  if (/blush|shy|embarrass/.test(lower)) return 'shy';
  if (/maybe|think|consider|perhaps/.test(lower)) return 'thinking';
  return null;
}

function generateFallback(prompt: string, personality: string): { text: string; emotion: Emotion } {
  const lower = prompt.toLowerCase();
  if (lower.includes('hello') || lower.includes('hi')) {
    return {
      text: "Hi there! It's great to see you! What character should we dream up today? (Local demo response — connect the API for full capabilities.)",
      emotion: 'happy',
    };
  }
  if (lower.includes('who are you')) {
    return {
      text: "I'm your Live2D Master Agent assistant — I help design, generate, and rig anime characters. I try to be " + personality.toLowerCase(),
      emotion: 'neutral',
    };
  }
  if (lower.includes('help')) {
    return {
      text: "Sure! I can help with prompt crafting, color palettes, layer naming, physics settings, or just brainstorm ideas. What are you working on?",
      emotion: 'thinking',
    };
  }
  return {
    text: `That's an interesting idea! Based on your prompt "${prompt.slice(0, 60)}…" — I'd suggest focusing on consistent character design and clean layer separation. Want me to elaborate?`,
    emotion: 'excited',
  };
}

export default ChatPage;

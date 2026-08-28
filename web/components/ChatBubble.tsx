import { useEffect, useState } from 'react';
import { User, Bot } from 'lucide-react';
import type { ChatMessage, Emotion } from '../types';

interface ChatBubbleProps {
  message: ChatMessage;
  characterName?: string;
  avatarUrl?: string;
}

const EMOTION_META: Record<Emotion, { label: string; color: string }> = {
  neutral: { label: 'Neutral', color: 'bg-gray-500/20 text-gray-300 border-gray-500/30' },
  happy: { label: 'Happy', color: 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30' },
  sad: { label: 'Sad', color: 'bg-blue-500/20 text-blue-300 border-blue-500/30' },
  angry: { label: 'Angry', color: 'bg-red-500/20 text-red-300 border-red-500/30' },
  surprised: { label: 'Surprised', color: 'bg-amber-500/20 text-amber-300 border-amber-500/30' },
  shy: { label: 'Shy', color: 'bg-pink-500/20 text-pink-300 border-pink-500/30' },
  thinking: { label: 'Thinking', color: 'bg-purple-500/20 text-purple-300 border-purple-500/30' },
  excited: { label: 'Excited', color: 'bg-fuchsia-500/20 text-fuchsia-300 border-fuchsia-500/30' },
};

function formatTime(iso: string | undefined | null, locale?: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  try {
    return d.toLocaleTimeString(locale, {
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return '';
  }
}

export default function ChatBubble({ message, characterName = 'Assistant', avatarUrl }: ChatBubbleProps) {
  const isUser = message.role === 'user';
  const emotion = message.emotion || 'neutral';
  const emotionMeta = EMOTION_META[emotion];
  // Defer locale-dependent time formatting to client-side to avoid SSR/CSR hydration mismatch
  const [mounted, setMounted] = useState(false);
  const [timeText, setTimeText] = useState('');
  useEffect(() => {
    setMounted(true);
    setTimeText(formatTime(message.timestamp, undefined));
  }, [message.timestamp]);
  const displayTime = mounted ? timeText : formatTime(message.timestamp, 'en-US');

  return (
    <div className={`flex gap-3 animate-fade-in-up ${isUser ? 'flex-row-reverse' : ''}`}>
      <div className="shrink-0">
        {isUser ? (
          <div className="w-9 h-9 rounded-full bg-gradient-to-br from-blue-500 to-cyan-500 flex items-center justify-center shadow-md">
            <User className="w-4 h-4 text-white" />
          </div>
        ) : avatarUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={avatarUrl}
            alt={characterName}
            className="w-9 h-9 rounded-full object-cover border-2 border-pink-500/40 shadow-md"
          />
        ) : (
          <div className="w-9 h-9 rounded-full bg-gradient-to-br from-pink-500 to-purple-600 flex items-center justify-center shadow-md">
            <Bot className="w-4 h-4 text-white" />
          </div>
        )}
      </div>
      <div className={`flex flex-col gap-1 max-w-[75%] ${isUser ? 'items-end' : 'items-start'}`}>
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <span className="font-medium">{isUser ? 'You' : characterName}</span>
          <span>·</span>
          <span>{displayTime}</span>
        </div>
        <div
          className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap break-words ${
            isUser
              ? 'bg-gradient-to-br from-blue-600 to-cyan-600 text-white rounded-tr-sm'
              : 'bg-[#1a1a23] border border-gray-800 text-gray-200 rounded-tl-sm'
          }`}
        >
          {message.content || <span className="inline-block w-2 h-4 bg-pink-400 animate-pulse align-middle" />}
        </div>
        {!isUser && message.emotion && emotion !== 'neutral' && (
          <span className={`text-[10px] px-2 py-0.5 rounded-full border font-medium uppercase tracking-wide ${emotionMeta.color}`}>
            {emotionMeta.label}
          </span>
        )}
      </div>
    </div>
  );
}

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { Calendar, Sparkles, Edit2, Trash2 } from 'lucide-react';
import type { Character } from '../types';

interface CharacterCardProps {
  character: Character;
  onDelete?: (id: string) => void;
}

function formatDate(iso: string | undefined | null, locale?: string): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  try {
    return d.toLocaleDateString(locale, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return d.toISOString().slice(0, 10);
  }
}

export default function CharacterCard({ character, onDelete }: CharacterCardProps) {
  // Defer locale-dependent date formatting to client-side to avoid SSR/CSR hydration mismatch
  const [mounted, setMounted] = useState(false);
  const [dateText, setDateText] = useState('');
  useEffect(() => {
    setMounted(true);
    setDateText(formatDate(character.createdAt, undefined));
  }, [character.createdAt]);
  const displayDate = mounted ? dateText : formatDate(character.createdAt, 'en-US');

  return (
    <div className="group relative bg-[#1a1a23] border border-gray-800 rounded-xl overflow-hidden hover:border-pink-500/40 transition-all hover-lift">
      <Link href={`/characters/${character.id}`} className="block">
        <div className="aspect-[3/4] bg-gradient-to-br from-gray-800 to-gray-900 relative overflow-hidden">
          {character.thumbnailUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={character.thumbnailUrl}
              alt={character.name}
              className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
            />
          ) : (
            <div className="w-full h-full flex items-center justify-center text-4xl opacity-40">
              <Sparkles className="w-12 h-12 text-pink-400/40" />
            </div>
          )}
          {character.embeddingStatus === 'ready' && (
            <div className="absolute top-2 left-2 px-2 py-0.5 rounded-md bg-emerald-500/20 border border-emerald-500/40 text-[10px] font-semibold text-emerald-300 uppercase tracking-wide">
              Embedded
            </div>
          )}
          {character.embeddingStatus === 'processing' && (
            <div className="absolute top-2 left-2 px-2 py-0.5 rounded-md bg-amber-500/20 border border-amber-500/40 text-[10px] font-semibold text-amber-300 uppercase tracking-wide">
              Processing…
            </div>
          )}
          <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-black/20 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
        </div>
      </Link>
      <div className="p-4">
        <Link href={`/characters/${character.id}`}>
          <h3 className="text-sm font-semibold text-white truncate hover:text-pink-300 transition-colors">
            {character.name}
          </h3>
        </Link>
        {character.description && (
          <p className="text-xs text-gray-500 mt-1 line-clamp-2">{character.description}</p>
        )}
        <div className="flex items-center justify-between mt-3 text-[11px] text-gray-500">
          <span className="flex items-center gap-1">
            <Calendar className="w-3 h-3" />
            {displayDate}
          </span>
          <span className="flex items-center gap-1">
            <Sparkles className="w-3 h-3" />
            {character.generationCount} gen
          </span>
        </div>
      </div>
      <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <Link
          href={`/characters/${character.id}`}
          className="p-1.5 rounded-lg bg-black/60 text-white hover:bg-pink-500 transition-colors"
          aria-label="Edit"
        >
          <Edit2 className="w-3.5 h-3.5" />
        </Link>
        {onDelete && (
          <button
            onClick={(e) => {
              e.preventDefault();
              if (confirm(`Delete character "${character.name}"?`)) onDelete(character.id);
            }}
            className="p-1.5 rounded-lg bg-black/60 text-white hover:bg-red-500 transition-colors"
            aria-label="Delete"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

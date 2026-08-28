import { Loader2 } from 'lucide-react';

interface LoadingSpinnerProps {
  size?: number;
  className?: string;
  label?: string;
}

export default function LoadingSpinner({ size = 24, className = '', label }: LoadingSpinnerProps) {
  return (
    <div className={`flex items-center justify-center gap-2 ${className}`}>
      <Loader2 className="animate-spin text-pink-400" style={{ width: size, height: size }} />
      {label && <span className="text-sm text-gray-400">{label}</span>}
    </div>
  );
}

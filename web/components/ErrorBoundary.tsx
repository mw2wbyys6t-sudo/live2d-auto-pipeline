import { Component, ReactNode, ErrorInfo } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    if (process.env.NODE_ENV === 'development') {
      console.error('ErrorBoundary caught:', error, errorInfo);
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        this.props.fallback || (
          <div className="min-h-screen flex items-center justify-center bg-[#0f0f13] text-white p-4">
            <div className="text-center max-w-md">
              <div className="text-6xl mb-4">😵</div>
              <h2 className="text-xl font-bold text-red-400 mb-2">出错了</h2>
              <p className="text-gray-400 text-sm mb-4">
                应用遇到了意外错误，请刷新页面重试
              </p>
              <button
                  onClick={() => {
                    const url = new URL(window.location.href);
                    url.searchParams.delete('token');
                    url.searchParams.delete('code');
                    window.location.href = url.toString();
                  }}
                  className="px-4 py-2 bg-pink-500 hover:bg-pink-600 rounded-lg text-sm transition-colors focus-visible:ring-2 focus-visible:ring-pink-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0f0f13]"
                >
                  刷新页面
                </button>
            </div>
          </div>
        )
      );
    }

    return this.props.children;
  }
}

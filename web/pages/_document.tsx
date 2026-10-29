import { Html, Head, Main, NextScript } from 'next/document';
import { APP_NAME } from '../lib/brand';

export default function Document() {
  return (
    <Html lang="zh-CN" className="dark">
      <Head>
        <meta charSet="utf-8" />
        <meta name="author" content={APP_NAME} />
        <meta name="theme-color" content="#0f0f13" />
        <meta name="color-scheme" content="dark" />
        <meta httpEquiv="X-UA-Compatible" content="IE=edge" />
        <link rel="icon" href="/favicon.ico" sizes="any" />
      </Head>
      <body>
        <Main />
        <NextScript />
      </body>
    </Html>
  );
}

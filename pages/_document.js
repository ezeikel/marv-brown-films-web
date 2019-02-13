import Document, { Head, Main, NextScript } from 'next/document';
// Import styled components ServerStyleSheet
import { ServerStyleSheet } from 'styled-components';

export default class MyDocument extends Document {
  static getInitialProps({ renderPage }) {
    // Step 1: Create an instance of ServerStyleSheet
    const sheet = new ServerStyleSheet();

    // Step 2: Retrieve styles from components in the page
    const page = renderPage(App => props => sheet.collectStyles(<App {...props} />));

    // Step 3: Extract the styles as <style> tags
    const styleTags = sheet.getStyleElement();

    // Step 4: Pass styleTags as a prop
    return { ...page, styleTags };
  }

  setGoogleAnalyticsTags() {
    return {
      __html: `
        window.dataLayer = window.dataLayer || [];
        function gtag(){dataLayer.push(arguments);}
        gtag('js', new Date());

        gtag('config', 'UA-134442589-1');
      `
    };
  }

  render() {
    return <html>
        <Head>
          {/* Step 5: Output the styles in the head  */}
          {this.props.styleTags}
          {/* Global site tag (gtag.js) - Google Analytics */}
          <script async src="https://www.googletagmanager.com/gtag/js?id=UA-131531376-1" />
          <script dangerouslySetInnerHTML={this.setGoogleAnalyticsTags()} />
        </Head>
        <body>
          <Main />
          <NextScript />
        </body>
      </html>;
  }
}

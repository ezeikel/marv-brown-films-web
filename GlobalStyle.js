import { createGlobalStyle } from "styled-components";

export default createGlobalStyle`
/* CSS Variables */
:root {
  /* Colors */
  --color-primary: #1abc9c;
  /* --color-primary: #F7E7CE; */
  --color-secondary: #F1D3D1;
  --color-tertiary: #4629D3;
  --color-radical-red: #FF3D63;
  --color-aquamarine: #62FFD3;
  --color-bittersweet: #FF7264;
  --color-black: #2E3333;
  --color-white: #FFFFFF;
  --color-gold: #CDA349;
  --color-gold-lighter: #D4AF61;
  --color-light-grey: #ECF0F1;
  --color-red: #E74C3C;
  --color-green: #2ECC71;
  --color-grey: #BDC3C7;
  --color-dark-grey: #9B9B9B;
  --color-black: #3b3b3b;
  /* Spacing */
  --spacing-tiny: 4px;
  --spacing-small: 8px;
  --spacing-medium: 16px;
  --spacing-large: 32px;
  --spacing-huge: 64px;
  --spacing-gargantuan: 128px;
  /* Font */
  --default-font-family: 'Lora', sans-serif;
  --default-font-size: 10px;
  --font-size-tiny: 1.4rem;
  --font-size-small: 1.6rem;
  --font-size-medium: 1.8rem;
  --font-size-large: 2rem;
  --font-size-huge: 2.2rem;

  --header-height: 130px;
  --header-height-wide: 100px;
  --header-footer: var(--header-height);
  --header-footer-wide: var( --header-height-wide);
}
::-moz-selection {
  background: var(--color-primary);
  color: var(--color-white);
}
::selection {
  background: var(--color-primary);
  color: var(--color-white);
}
* {
  box-sizing: border-box;
}
html {
  font-size: var(--default-font-size);
  scroll-behavior: smooth;
}
body {
  margin: 0;
  padding: 0;
  font-family: var(--default-font-family);
  font-size: 1.6rem;
}
img {
  max-width: 100%;
}
a {
  text-decoration: none;
  color: var(--color-black);
}
ul {
  list-style-type: none;
  padding-left: 0;
  margin: 0;
}
button {
  display: grid;
  place-items: center;
  padding: 0;
  border: none;
  outline: none;
  background: none;
  cursor: pointer;
  font-size: 1.6rem;
  font-family: var(--default-font-family);
}
/* Forms */
select {
  background-color: var(--color-white);
}
fieldset {
  border: none;
}
::-webkit-input-placeholder {
  /* Chrome/Opera/Safari */
  color: var(--color-light-grey);
  /*font-weight: 300;*/
}
::-moz-placeholder {
  /* Firefox 19+ */
  color: var(--color-light-grey);
  font-weight: 300;
}
:-ms-input-placeholder {
  /* IE 10+ */
  color: var(--color-light-grey);
  font-weight: 300;
}
:-moz-placeholder {
  /* Firefox 18- */
  color: var(--color-light-grey);
  font-weight: 300;
}
input, textarea {
  font-family: var(--font-family-default);
  font-size: var(--default)
}
input[type="text"], input[type="email"], input[type="tel"], input[type="date"], input[type="submit"], textarea, select {
  appearance: none;
  -webkit-appearance: none;
  -moz-appearance: none;
  outline: 0;
  border: 0;
  border-radius: 0;
  padding: 1em;
  border: 1px solid #ecf0f1;
  transition: all 0.3s ease-in-out;
  width: 100%;
  font-weight: 300;
}
button[type="submit"] {
  display: block;
  height: 100%;
  width: 100%;
  padding: 15px 90px;
  background-color: var(--color-primary);
  color: var(--color-white);
  transition: background-color 0.3s ease-in-out;
  text-transform: uppercase;
}
button[type="submit"][disabled] {
  opacity: 0.7;
  text-decoration: line-through;
}
`;

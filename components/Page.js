import GlobalStyle from "../GlobalStyle";
import { library } from "@fortawesome/fontawesome-svg-core";
import { fab } from "@fortawesome/free-brands-svg-icons";
import styled from "styled-components";
import Meta from "./Meta";
//import Header from './Header';
//import Footer from './Footer';

library.add(fab);

const Wrapper = styled.div`
  display: grid;
  place-items: center;
  min-height: 100vh;
`;

const Page = props => (
  <div>
    <Meta />
    <GlobalStyle />
    <Wrapper>
      {/* <Header /> */}
      {props.children}
      {/* <Footer /> */}
    </Wrapper>
  </div>
);

export default Page;
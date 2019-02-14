import styled from "styled-components";
import Link from "next/link";
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';

const Wrapper = styled.div`
  height: 100%;
  display: grid;
  align-content: start;
`;

const SocialLinks = styled.ul`
    display: flex;
    justify-content: center;
    li + li {
      margin-left: var(--spacing-huge);
    }
`;

const Index = () => (
  <Wrapper>
    <img src="/static/logos/logo-black.png" />
    <SocialLinks>
      <li>
        <Link href="https://instagram.com/marvbrownfilms">
          <a>
            <FontAwesomeIcon
              icon={["fab", "instagram"]}
              color="var(--color-black)"
              size="2x"
            />
          </a>
        </Link>
      </li>
      <li>
        <Link href="https://www.youtube.com/channel/UCTZKxc4EPDHv9FrmyGw5mjQ?view_as=subscriber">
          <a>
            <FontAwesomeIcon
              icon={["fab", "youtube"]}
              color="var(--color-black)"
              size="2x"
            />
          </a>
        </Link>
      </li>
      <li>
        <Link href="https://twitter.com/marvbrownfilms">
          <a>
            <FontAwesomeIcon
              icon={["fab", "twitter"]}
              color="var(--color-black)"
              size="2x"
            />
          </a>
        </Link>
      </li>
      <li>
        <Link href="https://www.facebook.com/pages/category/Video-Creator/Marv-Brown-Films-1300210650036123/">
          <a>
            <FontAwesomeIcon
              icon={["fab", "facebook-f"]}
              color="var(--color-black)"
              size="2x"
            />
          </a>
        </Link>
      </li>
    </SocialLinks>
  </Wrapper>
);

export default Index;

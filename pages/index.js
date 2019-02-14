import { Component } from 'react';
import Link from "next/link";
import YouTube from 'react-youtube';
import styled from "styled-components";
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';

const Wrapper = styled.div`
  display: grid;
  place-items: center;
  grid-row-gap: var(--spacing-huge);
  height: 100%;
  width: 100%;
  padding: var(--spacing-large) var(--spacing-medium);
`;

const Logo = styled.img`
  max-height: 20vh;
`;

const VideosWrapper = styled.section`
  display: grid;
  grid-row-gap: var(--spacing-large);
  align-items: center;
  width: 100%;
  iframe {
    width: 100%;
  }
  @media(min-width: 768px) {
    grid-template-columns: repeat(3, 1fr);
    grid-column-gap: var(--spacing-large);
  }
`;

const VideoWrapper = styled.div`
  display: grid;
  grid-row-gap: var(--spacing-medium);
`;

const VideoTitle = styled.p`
  display: grid;
  place-items: center;
  margin: 0;
`;

const ContactUs = styled.span`
  a {
    text-decoration: underline;
    color: var(--color-gold);
  }
`;

const SocialLinks = styled.ul`
  display: flex;
  justify-content: center;
  li + li {
    margin-left: var(--spacing-huge);
  }
`;

class Index extends Component {
  _onReady(event) {
    // access to player in all event handlers via event.target
    event.target.pauseVideo();
  }

  render() {
    const youtubeVideos = [
      {
        id: 'iymAop1vSdk',
        title: 'Cassandra + Karl'
      },
      {
        id: 'B0svEooXzW8',
        title: 'Priscilla + Sellasie'
      },
      {
        id: 'QJWgcEnNiyE',
        title: 'Swodei + Emmanuel'
      },
    ];
    const opts = {
      height: '360',
      width: '100%',
      playerVars: { // https://developers.google.com/youtube/player_parameters
        autoplay: 1,
        showinfo: 0, // deprecated
        modestbranding: 1
      }
    };

    return (
      <Wrapper>
        <Logo src="/static/logos/logo-black.png" />
        <VideosWrapper>
          {
            youtubeVideos.map(({ id, title}) => (
              <VideoWrapper>
                <YouTube
                  videoId={id}
                  opts={opts}
                  onReady={this._onReady}
                />
                <VideoTitle>{title}</VideoTitle>
              </VideoWrapper>
            ))
          }
        </VideosWrapper>
        <ContactUs>Contact us at <a href="mailto:info@marvbrownfilms.com">info@marvbrownfilms.com</a></ContactUs>
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
  }
}

export default Index;

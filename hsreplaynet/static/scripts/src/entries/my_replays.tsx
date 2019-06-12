import React from "react";
import ReactDOM from "react-dom";
import CardData from "../CardData";
import UserData from "../UserData";
import Fragments from "../components/Fragments";
import Root from "../components/Root";
import { cardArt, image } from "../helpers";
import MyReplays from "../pages/MyReplays";

UserData.create();
let username = UserData.getUsername();

// override username from url if available
const query = location.search;
const parts = query.split("&");
for (const part of parts) {
	const matches = part.match(/\??username=(.*)/);
	if (matches) {
		username = decodeURIComponent(matches[1]);
		break;
	}
}

const context = JSON.parse(
	document.getElementById("react_context").textContent,
);

const render = (cardData: CardData) => {
	ReactDOM.render(
		<Root>
			<Fragments
				defaults={{
					name: "",
					mode: "",
					format: "",
					result: "",
					season: "",
					hero: "ALL",
					opponent: "ALL",
				}}
				debounce={"name"}
			>
				<MyReplays
					image={image}
					cardArt={cardArt}
					cardData={cardData}
					username={username}
					totalGames={context["total_games"]}
				/>
			</Fragments>
		</Root>,
		document.getElementById("my_replays-container"),
	);
};

render(null);

new CardData().load(render);

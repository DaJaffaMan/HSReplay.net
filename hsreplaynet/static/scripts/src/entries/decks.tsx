import React from "react";
import ReactDOM from "react-dom";
import CardData from "../CardData";
import Decks from "../pages/Decks";
import UserData from "../UserData";
import Fragments from "../components/Fragments";
import ErrorReporter from "../components/ErrorReporter";
import { HearthstoneCollection } from "../interfaces";
import DataManager from "../DataManager";

const container = document.getElementById("decks-container");
UserData.create();

const render = (
	cardData: CardData,
	collection: HearthstoneCollection | null
) => {
	ReactDOM.render(
		<ErrorReporter>
			<Fragments
				defaults={{
					archetypeSelector: "",
					archetypes: [],
					excludedCards: [],
					gameType: "RANKED_STANDARD",
					includedCards: [],
					includedSet: "ALL",
					maxDustCost: -1,
					opponentClasses: [],
					playerClasses: [],
					rankRange: "ALL",
					region: "ALL",
					timeRange: UserData.hasFeature("current-patch-filter")
						? "CURRENT_PATCH"
						: UserData.hasFeature("current-expansion-filter")
							? "CURRENT_EXPANSION"
							: "LAST_30_DAYS",
					trainingData: "",
					withStream: false
				}}
				immutable={
					!UserData.isPremium()
						? ["account", "opponentClass", "rankRange", "region"]
						: null
				}
			>
				<Decks
					cardData={cardData}
					collection={collection}
					latestSet="LOOTAPALOOZA"
					promoteLatestSet={UserData.hasFeature(
						"current-expansion-filter"
					)}
				/>
			</Fragments>
		</ErrorReporter>,
		container
	);
};

render(null, null);

let myCardData = null;
let myCollectionData = null;

new CardData().load(cardData => {
	myCardData = cardData;
	render(myCardData, myCollectionData);
});

if (UserData.isAuthenticated() && UserData.hasFeature("max-dust-filter")) {
	DataManager.get("/api/v1/collection/").then(collection => {
		myCollectionData = collection;
		render(myCardData, myCollectionData);
	});
}

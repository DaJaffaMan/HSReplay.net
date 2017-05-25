import * as React from "react";
import HDTButton from "./HDTButton";
import UserData from "../UserData";
import Tooltip from "./Tooltip";
import CopyDeckButton from "./CopyDeckButton";
import CardData from "../CardData";

interface SwitchableCopyDeckButtonProps extends React.ClassAttributes<SwitchableCopyDeckButton> {
	// element
	cardData: CardData;
	disabled?: boolean;

	// deck specific
	name: string;
	cards: number[];
	heroes: number[];
	format: number;
	sourceUrl: string;

	// to be removed
	id?: string;
	cardIds: string[];
	deckClass: string;
}

export default class SwitchableCopyDeckButton extends React.Component<SwitchableCopyDeckButtonProps, void> {
	render() {
		const userdata = new UserData();

		if (!userdata.hasFeature("deckstrings")) {
			return (
				<HDTButton
					card_ids={this.props.cardIds}
					deckClass={this.props.deckClass}
					disabled={this.props.disabled}
					id={this.props.id}
					name={this.props.name}
					sourceUrl={this.props.sourceUrl}
				/>
			);
		}

		return <CopyDeckButton
			cardData={this.props.cardData}
			cards={this.props.cards}
			heroes={this.props.heroes}
			format={this.props.format}
			sourceUrl={this.props.sourceUrl}
			disabled={this.props.disabled}
		/>;
	}
}

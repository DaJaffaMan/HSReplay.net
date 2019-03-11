import React from "react";
import { getArchetypeUrl, image } from "../../../helpers";
import CardData from "../../../CardData";
import { ArchetypeRankPopularity } from "../../../interfaces";
import ArchetypeSignatureTooltip from "../ArchetypeSignatureTooltip";
import OtherArchetype from "../OtherArchetype";

interface Props {
	archetypeData?: ArchetypeRankPopularity;
	cardData: CardData;
	gameType: string;
	style?: any;
}

export default class RowHeader extends React.Component<Props> {
	public render(): React.ReactNode {
		return (
			<div className="matchup-row-header" style={this.props.style}>
				<div className="archetype">
					<div className="class-icon-wrapper">
						<img
							className="class-icon"
							src={image(
								`64x/class-icons/${this.props.archetypeData.playerClass.toLowerCase()}.png`,
							)}
						/>
					</div>
					{this.renderName()}
				</div>
			</div>
		);
	}

	renderName(): React.ReactNode {
		const { archetypeData } = this.props;
		if (archetypeData.id < 0) {
			return (
				<OtherArchetype
					name={archetypeData.name}
					playerClass={archetypeData.playerClass}
				/>
			);
		}
		return (
			<a
				href={getArchetypeUrl(archetypeData.id, archetypeData.name)}
				target="_blank"
			>
				<ArchetypeSignatureTooltip
					key={archetypeData.id}
					cardData={this.props.cardData}
					archetypeId={archetypeData.id}
					archetypeName={archetypeData.name}
					gameType={this.props.gameType}
				>
					<span className="archetype-name">{archetypeData.name}</span>
				</ArchetypeSignatureTooltip>
			</a>
		);
	}
}

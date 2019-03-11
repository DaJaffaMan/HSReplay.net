import React from "react";
import CardData from "../../CardData";
import DataManager from "../../DataManager";
import { ArchetypeSignature as ApiArchetypeSignature } from "../../utils/api";
import LoadingSpinner from "../LoadingSpinner";
import Tooltip from "../Tooltip";
import ArchetypeSignature from "../archetypedetail/ArchetypeSignature";
import { WithTranslation, withTranslation } from "react-i18next";

interface Props extends WithTranslation {
	cardData: CardData;
	archetypeName: string;
	archetypeId: number;
	gameType: string;
}

interface State {
	signature: ApiArchetypeSignature;
}

class ArchetypeSignatureTooltip extends React.Component<Props, State> {
	constructor(props: Props, context?: any) {
		super(props, context);
		this.state = {
			signature: null,
		};
	}

	fetchArchetypeData() {
		if (this.state.signature) {
			return;
		}
		const { archetypeId, gameType } = this.props;
		DataManager.get("/api/v1/archetypes/" + archetypeId).then(data => {
			const signature =
				gameType === "RANKED_WILD"
					? data.wild_signature
					: data.standard_signature;
			this.setState({ signature });
		});
	}

	public render(): React.ReactNode {
		return (
			<Tooltip
				id="tooltip-archetype-signature"
				content={this.renderTooltip()}
				header={this.props.archetypeName}
				onHovering={() => this.fetchArchetypeData()}
				xOffset={50}
			>
				{this.props.children}
			</Tooltip>
		);
	}

	renderTooltip(): React.ReactNode {
		if (!this.state.signature || !this.props.cardData) {
			return <LoadingSpinner active small />;
		}
		const { t } = this.props;
		return (
			<div>
				<ArchetypeSignature
					cardData={this.props.cardData}
					signature={this.state.signature}
					maxCards={20}
				/>
				<p>{t("Click to view archetype details")}</p>
			</div>
		);
	}
}

export default withTranslation()(ArchetypeSignatureTooltip);
